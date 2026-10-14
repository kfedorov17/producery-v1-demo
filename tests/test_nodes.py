"""Node-level tests without a model call.

Routing, the deterministic stages, the compliance gate on edits and the memory write policies, run through a real
compiled graph with an InMemoryStore so `get_store()` works.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from producery.agents.config import AgentsConfig
from producery.agents.factory import AgentFactory
from producery.graphs.crew import build_graph
from producery.graphs.deps import CrewDeps
from producery.guards.compliance import ComplianceGate
from producery.guards.heuristics import HeuristicClassifier
from producery.model.door import build_model_access
from producery.prompts.hub import LocalPromptSource
from producery.settings import Settings
from producery.store.records import Records
from producery.tools.registry import ToolRegistry


class FakeDB:
    """Just enough of Database for the model-free nodes."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, Any]] = []

    async def fetch(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        if "from households" in sql:
            return [{"email": "sam@example.net"}]
        if "from proposals" in sql:
            return [{"id": "prop-x", "status": "pending", "kind": "create_task", "payload": {}}]
        return []

    async def fetchone(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        rows = await self.fetch(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql: str, params: Any = None) -> None:
        self.rows.append((sql, params))

    def connection(self):
        """Transactional path used by Records.execute_proposal — a fake connection over the same fake rows."""
        from contextlib import asynccontextmanager

        db = self

        class _Cursor:
            def __init__(self, row):
                self._row = row

            async def fetchone(self):
                return self._row

        class _Conn:
            def transaction(self):
                return asynccontextmanager(self._noop)()

            async def _noop(self):
                yield None

            async def execute(self, sql: str, params: Any = None):
                db.rows.append((sql, params))
                return _Cursor(await db.fetchone(sql, params))

        @asynccontextmanager
        async def _ctx():
            yield _Conn()

        return _ctx()


def _deps() -> CrewDeps:
    s = Settings(producery_env="test", prompts_source="local", _env_file=None)
    cfg = AgentsConfig.load(s.config_dir / "agents.yaml")
    prompts = LocalPromptSource(s)
    door = build_model_access(s)
    reg = ToolRegistry.empty()
    return CrewDeps(
        settings=s,
        agents=cfg,
        models=door,
        prompts=prompts,
        tools=reg,
        factory=AgentFactory(cfg, door, prompts, reg),
        records=Records(FakeDB()),  # type: ignore[arg-type]
        memory_ttl_days={},
        compliance=ComplianceGate.load(s.config_dir / "compliance_rules.yaml"),
        heuristics=HeuristicClassifier.load(s.config_dir / "heuristics.yaml"),
        extraction_fields={"verify_below_confidence": 0.7},
    )


def _graph(store: InMemoryStore):
    return build_graph(_deps()).compile(checkpointer=InMemorySaver(), store=store)


@pytest.mark.asyncio
async def test_carrier_mail_routes_to_task_proposal_and_pauses_then_executes_on_approve():
    store = InMemoryStore()
    g = _graph(store)
    cfg = {"configurable": {"thread_id": "crew:t1"}}
    email = {
        "id": "t1",
        "from_email": "appointments@harborspecialty.example",
        "from_name": "Harbor",
        "subject": "docs",
        "body": "upload the W-9",
    }
    await g.ainvoke({"email": email}, config=cfg)
    snap = await g.aget_state(cfg)
    assert snap.next == ("apply_decision",) and snap.interrupts, "pauses before the consequential action"
    assert snap.values["classification"]["classification"] == "carrier_thread"
    assert snap.values["path"] == [
        "intake",
        "recall_memory",
        "classify",
        "task_proposal",
        "compliance_gate",
        "stage_proposal",
    ]
    await g.ainvoke(Command(resume={"action": "approve", "by": "sam"}), config=cfg)
    final = await g.aget_state(cfg)
    assert final.values["outcome"] == "create_task:approved:executed"
    episodes = await store.asearch(("memory", "episodic", "lakeview", "crew"))
    assert len(episodes) == 1 and episodes[0].value["sender"] == "appointments@harborspecialty.example"


@pytest.mark.asyncio
async def test_classifier_fallback_routes_to_manual_triage_not_the_deep_agent():
    g = _graph(InMemoryStore())
    nodes = g.nodes  # the conditional edge function is pure; test it through the compiled graph's route
    from producery.graphs.nodes import CrewNodes

    n = CrewNodes(_deps())
    state = {
        "email": {"attachment_document_id": None},
        "classification": {"classification": "client_service", "source": "fallback"},
    }
    assert n.route_after_classify(state) == "task_proposal"  # type: ignore[arg-type]
    state["classification"]["source"] = "model"
    assert n.route_after_classify(state) == "respond"  # type: ignore[arg-type]
    assert nodes  # graph compiled


@pytest.mark.asyncio
async def test_reject_with_reason_writes_procedural_memory_and_eval_threads_are_namespaced_apart():
    store = InMemoryStore()
    g = _graph(store)
    cfg = {"configurable": {"thread_id": "eval:t2"}}
    email = {
        "id": "t2",
        "from_email": "statements@keystonemutual.example",
        "from_name": "KM",
        "subject": "commission statement",
        "body": "remittance attached",
    }
    await g.ainvoke({"email": email}, config=cfg)
    await g.ainvoke(
        Command(resume={"action": "reject", "by": "eval", "reason": "bookkeeper files these"}), config=cfg
    )
    final = await g.aget_state(cfg)
    assert final.values["outcome"] == "rejected"
    assert len(await store.asearch(("memory", "procedural", "lakeview", "eval"))) == 1
    assert await store.asearch(("memory", "procedural", "lakeview", "crew")) == [], (
        "eval memory never lands in the crew namespace"
    )


@pytest.mark.asyncio
async def test_personal_mail_is_archived_without_storing_the_sender():
    store = InMemoryStore()
    g = _graph(store)
    cfg = {"configurable": {"thread_id": "crew:t3"}}
    email = {
        "id": "t3",
        "from_email": "ray@familymail.example",
        "from_name": "Ray",
        "subject": "golf?",
        "body": "tee time at 8",
    }
    # force the privacy route without a model: heuristics return None for this mail, so stub the classification
    n_deps = _deps()
    from producery.graphs import nodes as nodes_mod

    async def fake_classify(self, state):
        return {
            "classification": {
                "classification": "personal_ignore",
                "confidence": 0.99,
                "reason": "personal",
                "source": "model",
            },
            "path": ["classify"],
        }

    original = nodes_mod.CrewNodes.classify
    nodes_mod.CrewNodes.classify = fake_classify  # type: ignore[method-assign]
    try:
        g = build_graph(n_deps).compile(checkpointer=InMemorySaver(), store=store)
        await g.ainvoke({"email": email}, config=cfg)
    finally:
        nodes_mod.CrewNodes.classify = original  # type: ignore[method-assign]
    final = await g.aget_state(cfg)
    assert final.values["outcome"] == "archived_personal"
    ep = await store.asearch(("memory", "episodic", "lakeview", "crew"))
    assert len(ep) == 1 and ep[0].value["sender"] is None and "(withheld)" in ep[0].value["text"]


def test_compliance_gate_flags_an_edited_payload():
    gate = ComplianceGate.load(
        Settings(producery_env="test", _env_file=None).config_dir / "compliance_rules.yaml"
    )
    assert gate.check("Hi Dana, your premium will be $3,200 next term") != []
