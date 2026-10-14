"""Node-level, routing, reducer and partial-execution tests per the LangGraph testing guide.

Following https://docs.langchain.com/oss/python/langgraph/test: a fresh graph per test, an InMemorySaver per test,
single nodes through `compiled.nodes[...]`, sections through `update_state(as_node=...)` + `interrupt_after`.
No model, no network: stage agents go through the injected `structured` seam.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from producery.contracts import Classification, Extraction, FieldValue
from producery.graphs.crew import build_graph
from producery.graphs.nodes import CrewNodes
from producery.graphs.state import merge_dicts
from tests.test_nodes import FakeDB, _deps


class FakeStructured:
    """StructuredCaller fake: returns the configured schema instance (or raises) instead of calling a model."""

    def __init__(self, by_agent: dict[str, Any]) -> None:
        self.by_agent = by_agent
        self.calls: list[str] = []

    def __call__(self, agent: Any, schema: Any):
        self.calls.append(agent.name)
        outcome = self.by_agent[agent.name]

        class _Runnable:
            async def ainvoke(self, messages, config=None):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        return _Runnable()


def _graph(deps=None, store=None):
    deps = deps or _deps()
    return build_graph(deps).compile(checkpointer=InMemorySaver(), store=store or InMemoryStore()), deps


# --------------------------------------------------------------------------- routing as pure functions
@pytest.mark.parametrize(
    "classification,attachment,expected",
    [
        ({"classification": "client_service", "source": "fallback"}, None, "task_proposal"),
        ({"classification": "personal_ignore", "source": "model"}, None, "archive"),
        ({"classification": "client_service", "source": "model"}, "doc-1", "extract_document"),
        ({"classification": "prospect", "source": "model"}, None, "respond"),
        ({"classification": "client_service", "source": "heuristic"}, None, "respond"),
        ({"classification": "carrier_thread", "source": "heuristic"}, None, "task_proposal"),
        ({"classification": "billing_commission", "source": "model"}, None, "task_proposal"),
    ],
)
def test_route_after_classify_every_branch(classification, attachment, expected):
    n = CrewNodes(_deps())
    state: Any = {"email": {"attachment_document_id": attachment}, "classification": classification}
    assert n.route_after_classify(state) == expected


@pytest.mark.parametrize(
    "decision,expected",
    [
        ({"action": "approve"}, "execute"),
        ({"action": "edit", "payload": {"body": "x"}}, "execute"),
        ({"action": "reject", "reason": "no"}, "record_rejection"),
        ({"action": "skip"}, "remember"),
        (None, "remember"),
    ],
)
def test_route_after_decision_every_branch(decision, expected):
    n = CrewNodes(_deps())
    assert n.route_after_decision({"decision": decision}) == expected  # type: ignore[arg-type]


# --------------------------------------------------------------------------- reducers
def test_merge_dicts_reducer_merges_concurrent_updates_in_one_superstep():
    class S(TypedDict, total=False):
        artifacts: Annotated[dict[str, str], merge_dicts]
        path: Annotated[list[str], operator.add]

    def a(state: S):
        return {"artifacts": {"a": "1"}, "path": ["a"]}

    def b(state: S):
        return {"artifacts": {"b": "2"}, "path": ["b"]}

    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge(START, "b")  # parallel branches touch the same channels in one superstep
    g.add_edge("a", END)
    g.add_edge("b", END)
    out = g.compile().invoke({"artifacts": {"seed": "0"}, "path": []})
    assert out["artifacts"] == {"seed": "0", "a": "1", "b": "2"}
    assert sorted(out["path"]) == ["a", "b"]
    assert merge_dicts(None, {"x": 1}) == {"x": 1} and merge_dicts({"x": 1}, {"x": 2}) == {"x": 2}


# --------------------------------------------------------------------------- single nodes
@pytest.mark.asyncio
async def test_compliance_gate_node_flags_only_client_text():
    g, _ = _graph()
    flagged = await g.nodes["compliance_gate"].ainvoke(
        {
            "proposal": {
                "kind": "send_email",
                "payload": {"subject": "x", "body": "your premium will be $2,150"},
            }
        }
    )
    assert {f["code"] for f in flagged["compliance_flags"]} >= {"premium_in_client_text", "rate_prediction"}
    clean = await g.nodes["compliance_gate"].ainvoke(
        {"proposal": {"kind": "create_task", "payload": {"title": "premium $2,150 — internal"}}}
    )
    assert clean["compliance_flags"] == []


@pytest.mark.asyncio
async def test_task_proposal_and_archive_nodes_are_deterministic():
    g, _ = _graph()
    tp = await g.nodes["task_proposal"].ainvoke(
        {
            "email": {"subject": "statement", "from_email": "x@y.example"},
            "classification": {
                "classification": "billing_commission",
                "confidence": 0.8,
                "reason": "remittance",
            },
        }
    )
    assert (
        tp["proposal"]["kind"] == "create_task"
        and tp["proposal"]["payload"]["title"] == "[billing_commission] statement"
    )
    ar = await g.nodes["archive"].ainvoke({"email": {}})
    assert ar["outcome"] == "archived_personal" and ar["proposal"] == {}


@pytest.mark.asyncio
async def test_classify_node_uses_the_injected_seam_and_degrades_to_fallback():
    deps = _deps()
    deps.structured = FakeStructured(
        {"classifier": Classification(classification="prospect", confidence=0.91, reason="asks for a quote")}
    )
    g, _ = _graph(deps)
    out = await g.nodes["classify"].ainvoke(
        {"email": {"from_email": "new@person.example", "subject": "quote", "body": "I need insurance"}}
    )
    assert out["classification"] == {
        "classification": "prospect",
        "confidence": 0.91,
        "reason": "asks for a quote",
        "source": "model",
    }
    deps.structured = FakeStructured({"classifier": RuntimeError("503 all models failing")})
    out = await g.nodes["classify"].ainvoke(
        {"email": {"from_email": "new@person.example", "subject": "quote", "body": "I need insurance"}}
    )
    assert out["classification"]["source"] == "fallback" and "classifier unavailable" in out["error"]
    # live run UI-F5: the door answered but produced no structured object — degrade, never crash
    deps.structured = FakeStructured({"classifier": None})
    out = await g.nodes["classify"].ainvoke(
        {"email": {"from_email": "new@person.example", "subject": "quote", "body": "I need insurance"}}
    )
    assert out["classification"]["source"] == "fallback" and "no structured result" in out["error"]


@pytest.mark.asyncio
async def test_extract_document_node_builds_the_update_record_proposal_with_verify_list():
    deps = _deps()

    class DocDB(FakeDB):
        async def fetchone(self, sql, params=None):
            if "from documents" in sql:
                return {
                    "id": "doc-1",
                    "title": "Dec page",
                    "doc_type": "dec_page",
                    "body": "Policy HO-1 premium 1310",
                    "status": "ready",
                }
            return await super().fetchone(sql, params)

    from producery.store.records import Records

    deps.records = Records(DocDB())  # type: ignore[arg-type]
    deps.extraction_fields = {
        "verify_below_confidence": 0.7,
        "dec_page": {"policy_number": "the policy number", "annual_premium": "annual premium"},
        "contract": {},
    }
    deps.structured = FakeStructured(
        {
            "extractor": Extraction(
                doc_type="dec_page",
                fields={
                    "policy_number": FieldValue(value="HO-1", source_span="Policy HO-1", confidence=0.95),
                    "annual_premium": FieldValue(value=1310, source_span="premium 1310", confidence=0.4),
                },
            )
        }
    )
    g, _ = _graph(deps)
    out = await g.nodes["extract_document"].ainvoke(
        {"email": {"attachment_document_id": "doc-1", "from_name": "Wei Moreau"}}
    )
    p = out["proposal"]
    assert p["kind"] == "update_record" and p["payload"]["fields"] == {
        "policy_number": "HO-1",
        "annual_premium": 1310,
    }
    assert p["payload"]["verify_fields"] == ["annual_premium"]  # below the confidence floor → human verifies
    assert out["citations"] == ["[doc:doc-1]"]


# --------------------------------------------------------------------------- partial execution (update_state + interrupt_after)
@pytest.mark.asyncio
async def test_section_after_classify_runs_alone_with_seeded_state():
    g, _ = _graph()
    cfg = {"configurable": {"thread_id": "crew:partial"}}
    await g.aupdate_state(
        cfg,
        {
            "email": {
                "id": "p",
                "from_email": "appointments@harborspecialty.example",
                "subject": "docs",
                "body": "W-9",
            },
            "classification": {
                "classification": "carrier_thread",
                "confidence": 0.9,
                "reason": "carrier",
                "source": "heuristic",
            },
            "path": ["intake", "recall_memory", "classify"],
        },
        as_node="classify",
    )
    await g.ainvoke(None, config=cfg, interrupt_after=["compliance_gate"])
    snap = await g.aget_state(cfg)
    assert snap.values["path"] == ["intake", "recall_memory", "classify", "task_proposal", "compliance_gate"]
    assert snap.values["proposal"]["kind"] == "create_task" and snap.next == ("stage_proposal",)


@pytest.mark.asyncio
async def test_edit_decision_recheck_flags_and_writes_procedural_memory():
    store = InMemoryStore()
    g, deps = _graph(store=store)
    cfg = {"configurable": {"thread_id": "crew:edit"}}
    await g.ainvoke(
        {
            "email": {
                "id": "e",
                "from_email": "appointments@harborspecialty.example",
                "subject": "docs",
                "body": "W-9",
            }
        },
        config=cfg,
    )
    # the fake records return a send_email proposal id so the edit path re-checks client text
    deps.records.db.rows.clear()  # type: ignore[attr-defined]
    await g.ainvoke(
        Command(
            resume={
                "action": "edit",
                "by": "sam",
                "payload": {"subject": "re", "body": "Hi, your premium will be $2,150 next term"},
            }
        ),
        config=cfg,
    )
    final = await g.aget_state(cfg)
    assert final.values["outcome"] == "create_task:edited:executed"
    pro = await store.asearch(("memory", "procedural", "lakeview", "crew"))
    assert len(pro) == 1 and pro[0].value["origin"] == "human_edit"


@pytest.mark.asyncio
async def test_skip_path_when_nothing_to_propose_reaches_remember():
    deps = _deps()
    deps.structured = FakeStructured(
        {"classifier": Classification(classification="personal_ignore", confidence=0.99, reason="personal")}
    )
    g, _ = _graph(deps)
    cfg = {"configurable": {"thread_id": "crew:skip"}}
    await g.ainvoke(
        {"email": {"id": "s", "from_email": "ray@familymail.example", "subject": "golf", "body": "tee?"}}, cfg
    )
    snap = await g.aget_state(cfg)
    assert snap.next == () and snap.values["outcome"] == "archived_personal"
    assert snap.values["path"][-2:] == ["archive", "remember"]


class FakeDrafter:
    """Compiled deep-agent fake: returns the configured structured response without a model."""

    def __init__(self, response: Any) -> None:
        self.response = response

    async def ainvoke(self, payload, config=None):
        return {"structured_response": self.response}


@pytest.mark.asyncio
async def test_respond_turns_an_empty_draft_into_a_review_task_not_a_blank_email():
    """Regression run R4-1: an empty DraftResponse was staged and executed as a blank send_email."""
    from producery.contracts import DraftResponse

    deps = _deps()
    deps.drafter = FakeDrafter(DraftResponse(subject="", body="", owner_brief=""))
    g, _ = _graph(deps)
    state = {
        "email": {
            "id": "e1",
            "from_email": "wei.moreau@example.net",
            "subject": "policy number?",
            "body": "?",
        },
        "classification": {
            "classification": "client_service",
            "confidence": 0.9,
            "reason": "known",
            "source": "heuristic",
        },
    }
    out = await g.nodes["respond"].ainvoke(state)
    assert out["proposal"]["kind"] == "create_task"
    assert out["proposal"]["payload"]["detail"] == "drafter returned an empty draft"
    assert "EMPTY DRAFT" in out["proposal"]["owner_brief"]


@pytest.mark.asyncio
async def test_respond_keeps_a_real_draft_as_a_send_email():
    from producery.contracts import DraftResponse

    deps = _deps()
    deps.drafter = FakeDrafter(
        DraftResponse(
            subject="Your policy", body="Hi Wei, your producer will confirm.", owner_brief="no data"
        )
    )
    g, _ = _graph(deps)
    state = {
        "email": {
            "id": "e2",
            "from_email": "wei.moreau@example.net",
            "subject": "policy number?",
            "body": "?",
        },
        "classification": {
            "classification": "client_service",
            "confidence": 0.9,
            "reason": "known",
            "source": "heuristic",
        },
    }
    out = await g.nodes["respond"].ainvoke(state)
    assert out["proposal"]["kind"] == "send_email" and out["proposal"]["payload"]["body"].startswith("Hi Wei")
