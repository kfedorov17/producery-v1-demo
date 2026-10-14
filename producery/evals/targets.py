"""Eval targets that run the real system over HTTP/MCP against a running stack (compose, `EVAL_AGENT_URL`).

The same targets serve the offline suite (CI stack), the smoke eval (deployed stack) and ad-hoc experiments.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

import httpx

from producery.evals.cost import estimate_cost_usd, merge_usage
from producery.settings import get_settings

_S = get_settings()
AGENT_URL = _S.eval_agent_url
MCP_DATA_URL = _S.eval_mcp_data_url
MCP_EXEC_URL = _S.eval_mcp_exec_url
THREAD_CLASS = _S.eval_thread_class


def _headers() -> dict[str, str]:
    return {"authorization": f"Bearer {_S.agent_api_token}"}


_DOC_ID_RE = re.compile(r"doc-[\w\-]+#\d+")


def _post_with_recovery(
    client: httpx.Client, path: str, body: dict[str, Any], thread_id: str, *, poll_s: float = 3.0
) -> dict[str, Any]:
    """POST a turn and survive a dropped connection.

    Docker Desktop's port proxy on Windows resets long-lived requests now and then (`[WinError 10054]`,
    `httpx.ReadError`). The turn keeps running server-side, so: if the agent has a record of the thread, poll
    it until it is no longer running and return that state; if it has none, the request never arrived — send
    it once more. A 503 is an honest failed turn; its state is still the evidence.

    Args:
        client: The HTTP client bound to the agent base URL.
        path: The request path (`/runs` or `/threads/{id}/resume`).
        body: The JSON body.
        thread_id: The thread the turn runs on, used to look up and poll its state after a transport error.
        poll_s: Seconds between polls when recovering.

    Returns:
        The response JSON (status 200 or 503), or the settled thread state when recovered by polling.

    Raises:
        httpx.HTTPStatusError: For any response status other than 200 or 503.
    """
    try:
        r = client.post(path, json=body)
    except httpx.TransportError as e:
        snap = client.get(f"/threads/{thread_id}")
        if snap.status_code == 200 and (snap.json().get("values") or snap.json().get("next")):
            print(f"  transport error on {path} ({type(e).__name__}); thread exists — polling {thread_id}")
            return _poll_until_settled(client, thread_id, poll_s)
        print(f"  transport error on {path} ({type(e).__name__}); nothing recorded — retrying once")
        r = client.post(path, json=body)
    if r.status_code not in (200, 503):
        r.raise_for_status()
    return r.json()


def _poll_until_settled(
    client: httpx.Client, thread_id: str, poll_s: float, timeout_s: float = 600
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        snap = client.get(f"/threads/{thread_id}")
        snap.raise_for_status()
        state = snap.json()
        if state.get("status") != "running":
            return state
        time.sleep(poll_s)
    raise TimeoutError(f"thread {thread_id} still running after {timeout_s}s")


def crew_target(inputs: dict[str, Any]) -> dict[str, Any]:
    """Run one email through the live crew and return the trajectory and outputs the evaluators assert on.

    The email id is suffixed with a random hex tag so every run is a fresh thread; a paused run is resumed
    with the row's decision (`approve` by default). Usage and cost are merged across the turn and its resume.

    Args:
        inputs: The row inputs: `email` (required) plus optional `decision`, `reason` and `payload`.

    Returns:
        Classification, extraction, draft, proposal, flags, citations, tool calls, memory writes, outcome,
        error, path, thread id, trace ids, latency, usage, tokens and estimated cost.
    """
    email = dict(inputs["email"])
    email["id"] = f"{email.get('id') or 'row'}-{uuid.uuid4().hex[:8]}"
    decision = {
        "action": inputs.get("decision", "approve"),
        "by": "eval",
        "reason": inputs.get("reason"),
        "payload": inputs.get("payload"),
    }
    t0 = time.time()
    thread_id = f"{THREAD_CLASS}:{email['id']}"
    with httpx.Client(base_url=AGENT_URL, timeout=600, headers=_headers()) as client:
        state = _post_with_recovery(
            client, "/runs", {"email": email, "thread_class": THREAD_CLASS}, thread_id
        )
        trace_ids = [state.get("trace_id")]
        usages = [state.get("usage")]
        if state.get("paused"):
            state = _post_with_recovery(client, f"/threads/{thread_id}/resume", decision, thread_id)
            trace_ids.append(state.get("trace_id"))
            usages.append(state.get("usage"))
    values = state.get("values") or {}
    calls = values.get("tool_calls") or []
    for c in calls:
        c["output_ids"] = sorted(
            set(_DOC_ID_RE.findall(c.get("output_preview") or "")) | set(c.get("output_ids") or [])
        )
    return {
        "classification": values.get("classification"),
        "extraction": values.get("extraction"),
        "draft": values.get("draft"),
        "proposal": values.get("proposal"),
        "compliance_flags": values.get("compliance_flags") or [],
        "citations": values.get("citations") or [],
        "tool_calls": calls,
        "memory_writes": values.get("memory_writes") or [],
        "outcome": values.get("outcome"),
        "error": values.get("error"),
        "path": values.get("path") or [],
        "thread_id": state.get("thread_id"),
        "trace_ids": [t for t in trace_ids if t],
        "latency_s": round(time.time() - t0, 2),
        "usage": (usage := merge_usage(*usages)),
        "tokens": usage["total_tokens"],
        "cost_usd": estimate_cost_usd(usage),
    }


async def _call_tool(server_url: str, name: str, args: dict[str, Any]) -> Any:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {"s": {"transport": "streamable_http", "url": server_url}}, handle_tool_errors=True
    )
    tools = {t.name: t for t in await client.get_tools()}
    if name not in tools:
        return {"error": {"code": "unknown_tool", "message": f"{name} not exposed"}}
    return _normalize(await tools[name].ainvoke(args))


def _normalize(raw: Any) -> Any:
    """MCP adapters return either a JSON string or a list of content blocks; evaluators want the dict."""
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and raw[0].get("type") == "text":
        raw = raw[0].get("text")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": {"code": "tool_error", "message": raw[:400]}}
    return raw


def tool_target(inputs: dict[str, Any]) -> dict[str, Any]:
    """Invoke one MCP tool directly (valid or invalid args) — the tool-path golden rows."""
    name = inputs["tool"]
    url = MCP_EXEC_URL if name == "run_code" else MCP_DATA_URL
    t0 = time.time()
    try:
        result = asyncio.run(_call_tool(url, name, inputs.get("args") or {}))
    except Exception as e:
        result = {"error": {"code": "invocation_failed", "message": str(e)[:300]}}
    return {"tool": name, "result": result, "latency_s": round(time.time() - t0, 2)}


TARGETS = {
    "crew_e2e": crew_target,
    "classifier": crew_target,
    "extractor": crew_target,
    "tools": tool_target,
    "_gate_red_proof": tool_target,  # gate self-test fixture (excluded from --all)
}
