"""Degradation paths.

A tool-service outage degrades into a structured result, the Hub falls back to a fingerprint-matched local prompt
(and refuses a mismatch), dropped trace batches are counted, and trace masking removes identities but keeps domains.
"""

from __future__ import annotations

import json
import logging

import pytest
from langchain_core.tools import StructuredTool

from producery.graphs.recorder import ToolCallRecorder
from producery.observability import trace_delivery
from producery.observability.masking import _replacer, mask_text
from producery.prompts.hub import HubPromptSource, fingerprint, load_prompt_file
from producery.settings import Settings
from producery.tools.registry import UNAVAILABLE_CODE, degrading


# ----------------------------------------------------------------------------- tool outage → structured result
def _raising_tool(exc: BaseException) -> StructuredTool:
    async def _boom(query: str) -> str:
        raise exc

    return StructuredTool.from_function(coroutine=_boom, name="sql_query", description="fake data tool")


@pytest.mark.asyncio
async def test_transport_failure_becomes_a_tool_unavailable_result_not_an_exception():
    group = ExceptionGroup(
        "unhandled errors in a TaskGroup", [ConnectionRefusedError("mcp-data:8081 refused")]
    )
    tool = degrading(_raising_tool(group))
    out = await tool.ainvoke({"query": "select * from v_renewals"})
    err = json.loads(out)["error"]
    assert err["code"] == UNAVAILABLE_CODE
    assert "sql_query" in err["message"] and "data unavailable" in err["hint"]


@pytest.mark.asyncio
async def test_a_healthy_tool_passes_through_unchanged():
    async def _ok(query: str) -> str:
        return json.dumps({"rows": [], "citation": "computed from v_renewals"})

    inner = StructuredTool.from_function(coroutine=_ok, name="sql_query", description="fake")
    out = await degrading(inner).ainvoke({"query": "select 1"})
    assert json.loads(out)["citation"] == "computed from v_renewals"
    assert degrading(inner).name == "sql_query" and degrading(inner).args_schema is inner.args_schema


def test_recorder_marks_a_structured_error_result_as_a_failed_call():
    from uuid import uuid4

    rec = ToolCallRecorder()
    rid = uuid4()
    rec.on_tool_start({"name": "sql_query"}, "", run_id=rid, inputs={"query": "select 1"})
    rec.on_tool_end('{"error": {"code": "tool_unavailable", "message": "x", "hint": "y"}}', run_id=rid)
    assert rec.calls[0]["status"] == "error"
    rid2 = uuid4()
    rec.on_tool_start({"name": "sql_query"}, "", run_id=rid2, inputs={"query": "select 1"})
    rec.on_tool_end('{"rows": []}', run_id=rid2)
    assert rec.calls[1]["status"] == "ok"


# ----------------------------------------------------------------------------- Hub outage at startup
class _DeadHub:
    def pull_prompt(self, ref: str):
        raise ConnectionError(f"GET /commits/{ref}: connection refused")


def _settings(**kw) -> Settings:
    return Settings(producery_env="test", prompts_source="hub", _env_file=None, **kw)


def test_hub_unreachable_falls_back_to_the_local_file_only_when_the_fingerprint_matches(tmp_path):
    s = _settings()
    src = HubPromptSource(s, client=_DeadHub())
    import yaml

    pins = yaml.safe_load((s.config_dir / "prompts.yaml").read_text(encoding="utf-8"))["pins"]
    for name, pin in pins.items():
        spec = load_prompt_file(s.prompts_dir / f"{name}.yaml")
        assert fingerprint(spec) == pin["fingerprint"], (
            f"{name}: local file drifted from its pin — push first"
        )
    src.load()
    assert src.ready() and set(src.sources().values()) == {"local-fallback"}
    assert src.pins() == {n: p["commit"] for n, p in pins.items()}  # the pin is still the Hub commit
    assert "ANCHOR" in src.get("drafter").system or src.get("drafter").system  # content came from the file


def test_hub_unreachable_with_a_drifted_local_file_refuses_to_start(tmp_path, monkeypatch):
    s = _settings()
    # copy the prompts, corrupt one, point the source at the copy
    import shutil

    pdir = tmp_path / "prompts"
    shutil.copytree(s.prompts_dir, pdir)
    drafter = pdir / "drafter.yaml"
    drafter.write_text(
        drafter.read_text(encoding="utf-8").replace("Assignment", "Assignmen t", 1), encoding="utf-8"
    )
    s2 = Settings(producery_env="test", prompts_source="hub", prompts_dir=pdir, _env_file=None)
    with pytest.raises(RuntimeError, match="refusing to serve an unpinned prompt"):
        HubPromptSource(s2, client=_DeadHub()).load()


def test_hub_fallback_can_be_disabled():
    s = _settings(prompts_local_fallback=False)
    with pytest.raises(ConnectionError):
        HubPromptSource(s, client=_DeadHub()).load()


# ----------------------------------------------------------------------------- trace delivery counter
def test_trace_delivery_monitor_counts_only_ingest_failures():
    m = trace_delivery.TraceDeliveryMonitor()
    log = logging.getLogger("langsmith.client.test")
    log.addHandler(m)
    log.setLevel(logging.WARNING)
    try:
        log.warning(
            "Failed to multipart ingest runs: LangSmithRateLimitError: Rate limit exceeded for /runs/multipart"
        )
        log.warning("some unrelated warning")
        log.warning("Failed to batch ingest runs: HTTP 503")
    finally:
        log.removeHandler(m)
    snap = m.snapshot()
    assert snap["ingest_failures"] == 2 and snap["delivered"] is False
    assert "503" in snap["last_error"] and snap["last_failure_at"]
    assert trace_delivery.TraceDeliveryMonitor().snapshot()["delivered"] is True


def test_install_is_idempotent_and_attached_to_the_sdk_logger():
    a = trace_delivery.install()
    b = trace_delivery.install()
    assert a is b and a in logging.getLogger("langsmith.client").handlers


# ----------------------------------------------------------------------------- trace masking
def test_mask_text_removes_identities_but_keeps_domains():
    text = "From Sam Patel <sam.patel@example.net>, call 555-123-4567 or (415) 555 0199; SSN 123-45-6789."
    out = mask_text(text)
    assert "sam.patel" not in out and "***@example.net" in out
    assert "555-123-4567" not in out and "[phone]" in out and "[ssn]" in out
    assert _replacer("Sam Patel", ["inputs", "email", "from_name"]) == "[name]"
    assert (
        _replacer("What changed on my renewal?", ["inputs", "email", "body"]) == "What changed on my renewal?"
    )


def test_scrub_email_pseudonymises_the_sender_and_keeps_the_domain():
    from producery.evals.scrub import scrub_email

    email = {
        "from_email": "sam.patel@example.net",
        "from_name": "Sam Patel",
        "subject": "renewal",
        "body": "Hi, Sam Patel here (sam.patel@example.net, 555-123-4567). What changed on my renewal? - Sam",
    }
    out = scrub_email(email, salt="trace-1")
    assert out["from_email"].endswith("@example.net") and "sam" not in out["from_email"]
    assert (
        out["from_name"].startswith("Client ") and "Sam" not in out["body"] and "sam.patel" not in out["body"]
    )
    assert "[phone]" in out["body"] and out["from_email"] in out["body"]
    assert scrub_email(email, salt="trace-1") == out  # deterministic
