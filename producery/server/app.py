"""Serve the inbound crew over HTTP (FastAPI).

Runs the crew with the Postgres checkpointer and store and exposes the HITL resume door, thread state, feedback,
health and readiness. The same graph serves under LangSmith Deployment via `langgraph.json` without changes to graph
code.

Every mutating or state-revealing route requires the service token (`AGENT_API_TOKEN`); only `/health` and `/ready`
are open (probes). Runs can execute inline (`wait=true`, scripts and evals) or in the background (`wait=false`, the
worker path): a background run returns 202 immediately and clients poll `/threads/{id}`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.types import Command
from pydantic import BaseModel, Field

from producery.contracts import Decision
from producery.graphs.crew import build_crew
from producery.logging_setup import setup_logging
from producery.observability import trace_delivery
from producery.observability.masking import build_client, build_tracer
from producery.server.bootstrap import build_deps, embed_fn
from producery.server.ui_api import router as ui_router
from producery.settings import get_settings
from producery.store.db import Database

logger = logging.getLogger("agent")
FAILED_OUTCOMES = {"failed"}


class RunRequest(BaseModel):
    """Body of ``POST /runs``: an existing inbox id or an ad-hoc email, the thread class and the wait flag."""

    email_id: str | None = None
    email: dict[str, Any] | None = None
    thread_class: str = Field(default="crew", pattern="^(crew|eval|smoke)$")
    wait: bool = True  # False → background run (202); poll /threads/{id}


class FeedbackRequest(BaseModel):
    """Body of ``POST /threads/{thread_id}/feedback``: feedback key, score, comment and optional trace id."""

    key: str = "user_rating"
    score: float | None = None
    comment: str | None = None
    trace_id: str | None = None


async def require_token(request: Request) -> None:
    """Service-token check on every route except the probes. Fails closed when no token is configured."""
    expected = request.app.state.settings.agent_api_token
    if not expected:
        raise HTTPException(503, "AGENT_API_TOKEN is not configured; refusing to serve")
    header = request.headers.get("authorization", "")
    presented = (
        header[7:] if header.lower().startswith("bearer ") else request.headers.get("x-producery-token", "")
    )
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(401, "missing or invalid service token")


def _thread_config(deps, thread_id: str, **meta: Any) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": deps.agents.graph.name,
        "tags": ["producery", deps.agents.graph.name, f"rev:{deps.settings.revision_id}"],
        "metadata": deps.trace_metadata(thread_id, thread_class=thread_id.split(":", 1)[0], **meta),
        "recursion_limit": deps.agents.graph.recursion_limit,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the application state for the server's lifetime and tear it down on exit.

    Opens the database pool, the Postgres checkpointer and the Postgres store (with the embedding index and
    the TTL sweeper), builds the crew graph and the masked LangSmith client and tracer; on shutdown cancels
    background runs, stops the sweeper, closes the UI's arq pool if one was opened, and closes the rest in
    reverse.

    Args:
        app: The FastAPI application whose ``state`` is populated.

    Raises:
        RuntimeError: When ``AGENT_API_TOKEN`` is not set.
    """
    settings = get_settings()
    setup_logging("agent")
    trace_monitor = trace_delivery.install()  # counts the SDK's dropped-batch warnings
    if not settings.agent_api_token:
        raise RuntimeError("AGENT_API_TOKEN must be set: the approval door is authenticated")
    stack = AsyncExitStack()
    db = Database(settings.database_url)
    await db.open()
    checkpointer = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(settings.database_url))
    await checkpointer.setup()
    store = await stack.enter_async_context(
        AsyncPostgresStore.from_conn_string(
            settings.database_url,
            index={"dims": settings.embed_dims, "embed": embed_fn(settings), "fields": ["text"]},
            ttl={"default_ttl": None, "refresh_on_read": False, "sweep_interval_minutes": 60},
        )
    )
    await store.setup()
    await store.start_ttl_sweeper()  # retention for memory records that carry a TTL (config/retention.yaml)
    deps = await build_deps(settings, db=db)
    app.state.settings = settings
    app.state.db = db
    app.state.deps = deps
    app.state.checkpointer = checkpointer
    app.state.graph = build_crew(deps, checkpointer, store)
    app.state.trace_monitor = trace_monitor
    # one LangSmith client for every trace and feedback call; masked client-side
    app.state.ls_client = (
        build_client(
            settings.langsmith_api_key, settings.langsmith_endpoint, mask=settings.langsmith_mask_pii
        )
        if settings.tracing_enabled
        else None
    )
    app.state.tracer = (
        build_tracer(settings.langsmith_project, app.state.ls_client)
        if app.state.ls_client is not None
        else None
    )
    app.state.background = set()
    logger.info(
        "agent_ready graph=%s revision=%s trace_masking=%s prompts_source=%s",
        deps.agents.graph.name,
        settings.revision_id,
        settings.langsmith_mask_pii and settings.tracing_enabled,
        sorted(set(deps.prompts.sources().values())),
    )
    try:
        yield
    finally:
        for task in list(app.state.background):
            task.cancel()
        await store.stop_ttl_sweeper()
        if (pool := getattr(app.state, "job_queue", None)) is not None:
            await pool.close()  # the UI's lazily opened arq pool (ui_api._job_queue)
        await stack.aclose()
        await db.close()


app = FastAPI(title="producery-agent", lifespan=lifespan)


def _snapshot_payload(snap) -> dict[str, Any]:
    values = dict(snap.values or {})
    interrupts = [getattr(i, "value", i) for i in (snap.interrupts or ())]
    return {
        "next": list(snap.next or []),
        "paused": bool(snap.next) and bool(interrupts),
        "interrupt": interrupts[0] if interrupts else None,
        "values": values,
        "checkpoint_id": (snap.config or {}).get("configurable", {}).get("checkpoint_id"),
    }


def _failed(payload: dict[str, Any]) -> bool:
    """A turn failed only when its OUTCOME says so.

    `values.error` is an honest degradation marker (classifier fallback → triage task, memory unavailable)
    on turns that still reached a human: treating treating it as failure made the worker
    retry and dead-letter a thread that was correctly paused.
    """
    values = payload.get("values") or {}
    return values.get("outcome") in FAILED_OUTCOMES


async def _run(graph, deps, thread_id: str, input_value: Any, meta: dict[str, Any]) -> dict[str, Any]:
    config = _thread_config(deps, thread_id, **meta)
    run_id = uuid.uuid4()
    config["run_id"] = (
        run_id  # the root run id IS the LangSmith trace id — returned so callers can join traces
    )
    usage = UsageMetadataCallbackHandler()  # per-model token usage of this turn
    tracer = getattr(app.state, "tracer", None)
    # the masked tracer goes in explicitly so LangChain does not add an unmasked one from the environment
    config["callbacks"] = [usage, *([tracer] if tracer is not None else [])]
    await graph.ainvoke(input_value, config=config)
    snap = await graph.aget_state(config)
    payload = _snapshot_payload(snap)
    payload["thread_id"] = thread_id
    payload["trace_id"] = str(run_id)
    payload["trace_metadata"] = config["metadata"]
    payload["usage"] = usage_summary(usage.usage_metadata)
    return payload


def usage_summary(by_model: dict[str, Any]) -> dict[str, Any]:
    """{model: {input_tokens, output_tokens, total_tokens}} plus totals — what the eval cost metric reads."""
    models = {
        m: {k: int(v) for k, v in um.items() if k in ("input_tokens", "output_tokens", "total_tokens")}
        for m, um in (by_model or {}).items()
    }
    return {
        "models": models,
        "total_tokens": sum(m.get("total_tokens", 0) for m in models.values()),
        "llm_calls": None,  # not observable from usage metadata; the trace has the count
    }


async def _finish_email(deps, email_id: str, payload: dict[str, Any]) -> None:
    """Honest terminal status: a failed turn is recorded as failed (the worker retries, then dead-letters)."""
    if _failed(payload):
        await deps.records.set_email_status(email_id, "failed")
        await deps.records.audit(
            payload.get("thread_id", ""),
            "agent",
            "turn_failed",
            {"error": (payload["values"].get("error") or "")[:300], "trace_id": payload.get("trace_id")},
        )
        return
    await deps.records.set_email_status(email_id, "paused" if payload["paused"] else "done")


async def _execute_run(
    app_: FastAPI, thread_id: str, email_id: str, input_value: Any, meta: dict[str, Any]
) -> dict[str, Any]:
    deps = app_.state.deps
    try:
        result = await _run(app_.state.graph, deps, thread_id, input_value, meta)
    except Exception as e:
        await deps.records.set_email_status(email_id, "failed")
        await deps.records.audit(thread_id, "agent", "turn_failed", {"error": str(e)[:300]})
        logger.exception("run_failed thread=%s", thread_id)
        raise
    finally:
        await deps.records.release_thread(thread_id)
    await _finish_email(deps, email_id, result)
    return result


@app.post("/runs", dependencies=[Depends(require_token)])
async def start_run(req: RunRequest) -> Any:
    """Start or resume a crew run for an email (``POST /runs``).

    The thread id is ``<thread_class>:<email id>``. A thread whose checkpoint still has a next step is resumed
    instead of restarted; if it is already paused for a decision, the current snapshot is returned with
    ``resumed: already_paused`` and no run starts. With ``wait=false`` the run executes in the background and
    the route returns 202; with ``wait=true`` it returns the final snapshot (200), or 503 when the outcome is
    failed.

    Args:
        req: The run request.

    Raises:
        HTTPException: 404 when ``email_id`` is unknown, 422 when neither ``email_id`` nor ``email`` is given,
            409 when the thread already has a run in progress, 500 when an inline run raises.
    """
    deps = app.state.deps
    graph = app.state.graph
    if req.email_id:
        row = await deps.records.get_email(req.email_id)
        if row is None:
            raise HTTPException(404, f"email {req.email_id} not found")
        email = {
            k: row[k] for k in ("id", "from_email", "from_name", "subject", "body", "attachment_document_id")
        }
    elif req.email:
        email = req.email
        stable = f"{email.get('from_email', '')}|{email.get('subject', '')}".encode()
        email.setdefault("id", f"adhoc-{hashlib.sha1(stable).hexdigest()[:12]}")  # noqa: S324 — id derivation
    else:
        raise HTTPException(422, "email_id or email required")
    thread_id = f"{req.thread_class}:{email['id']}"
    config = _thread_config(deps, thread_id)
    existing = await graph.aget_state(config)
    if existing.next:  # crash-recovery / redelivery: continue from the checkpoint instead of starting over
        logger.info("run_resume thread=%s next=%s", thread_id, existing.next)
        if existing.interrupts:
            payload = _snapshot_payload(existing)
            payload["thread_id"] = thread_id
            payload["resumed"] = "already_paused"
            await deps.records.set_email_status(
                email["id"], "paused", thread_id
            )  # the inbox row must say so too
            return payload
        input_value: Any = None
        meta = {"email_id": email["id"], "resumed": True}
    else:
        input_value = {"email": email}
        meta = {"email_id": email["id"]}
    if not await deps.records.claim_thread(thread_id):  # one run per thread across every agent instance (R11)
        raise HTTPException(409, f"thread {thread_id} already has a run in progress")
    await deps.records.set_email_status(email["id"], "running", thread_id)
    if not req.wait:
        task = asyncio.create_task(_execute_run(app, thread_id, email["id"], input_value, meta))
        app.state.background.add(task)
        task.add_done_callback(app.state.background.discard)
        return JSONResponse({"thread_id": thread_id, "status": "running"}, status_code=202)
    try:
        result = await _execute_run(app, thread_id, email["id"], input_value, meta)
    except Exception as e:
        raise HTTPException(500, f"run failed: {e}") from e
    if _failed(result):
        return JSONResponse({**result, "status": "failed"}, status_code=503)
    return result


@app.post("/threads/{thread_id}/resume", dependencies=[Depends(require_token)])
async def resume(thread_id: str, decision: Decision) -> Any:
    """Resume a paused thread with a human decision (``POST /threads/{thread_id}/resume``).

    Returns the final snapshot (200), or 503 when the resumed turn's outcome is failed. The inbox row named by
    the thread id is set to its terminal status.

    Args:
        thread_id: The paused thread.
        decision: The human decision, passed to the graph as the interrupt's resume value.

    Raises:
        HTTPException: 409 when the thread is not paused at an interrupt.
    """
    deps = app.state.deps
    graph = app.state.graph
    config = _thread_config(deps, thread_id)
    snap = await graph.aget_state(config)
    if not snap.interrupts:
        raise HTTPException(409, f"thread {thread_id} is not paused (next={list(snap.next)})")
    result = await _run(
        graph, deps, thread_id, Command(resume=decision.model_dump()), {"decision": decision.action}
    )
    await _finish_email(deps, thread_id.split(":", 1)[-1], result)
    if _failed(result):
        return JSONResponse({**result, "status": "failed"}, status_code=503)
    return result


@app.post("/threads/{thread_id}/continue", dependencies=[Depends(require_token)])
async def continue_thread(thread_id: str) -> dict[str, Any]:
    """Crash recovery: resume a thread whose run was cut off mid-node (no new input).

    Args:
        thread_id: The thread to continue.

    Returns:
        The snapshot after the run (200).

    Raises:
        HTTPException: 409 when the thread has nothing to continue, or is paused for a decision (use /resume).
    """
    deps = app.state.deps
    graph = app.state.graph
    config = _thread_config(deps, thread_id)
    snap = await graph.aget_state(config)
    if not snap.next:
        raise HTTPException(409, f"thread {thread_id} has nothing to continue")
    if snap.interrupts:
        raise HTTPException(409, f"thread {thread_id} is paused for a decision; use /resume")
    return await _run(graph, deps, thread_id, None, {"continued": True})


@app.get("/threads/{thread_id}", dependencies=[Depends(require_token)])
async def get_thread(thread_id: str) -> dict[str, Any]:
    """Return the thread's current snapshot with a derived status (``GET /threads/{thread_id}``, 200).

    ``status`` is ``paused`` at an interrupt, ``running`` when a next step remains, otherwise ``failed`` or
    ``done`` by outcome.

    Args:
        thread_id: The thread id.
    """
    deps = app.state.deps
    snap = await app.state.graph.aget_state(_thread_config(deps, thread_id))
    payload = _snapshot_payload(snap)
    payload["thread_id"] = thread_id
    payload["metadata"] = snap.metadata
    if payload["paused"]:
        payload["status"] = "paused"
    elif payload["next"]:
        payload["status"] = "running"
    else:
        payload["status"] = "failed" if _failed(payload) else "done"
    return payload


@app.get("/threads/{thread_id}/history", dependencies=[Depends(require_token)])
async def get_history(thread_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return the thread's checkpoints, newest first (``GET /threads/{thread_id}/history``, 200).

    Args:
        thread_id: The thread id.
        limit: Maximum checkpoints returned.

    Returns:
        Per checkpoint: checkpoint_id, next, step and the state's path.
    """
    deps = app.state.deps
    out = []
    async for snap in app.state.graph.aget_state_history(_thread_config(deps, thread_id), limit=limit):
        out.append(
            {
                "checkpoint_id": snap.config["configurable"].get("checkpoint_id"),
                "next": list(snap.next),
                "step": (snap.metadata or {}).get("step"),
                "path": (snap.values or {}).get("path"),
            }
        )
    return out


@app.post("/threads/{thread_id}/feedback", dependencies=[Depends(require_token)])
async def feedback(thread_id: str, req: FeedbackRequest) -> dict[str, Any]:
    """Feedback capture wired into traces: lands on the LangSmith trace of this thread.

    When the request carries no trace id, the latest trace id in the thread's audit log is used. The feedback
    is created through the masked client built at startup (or one built on demand) and its id is returned
    (200).

    Args:
        thread_id: The thread the feedback is about.
        req: Feedback key, score, comment and optional trace id.

    Raises:
        HTTPException: 404 when no trace id was given and none is recorded for the thread.
    """
    settings = app.state.settings
    trace_id = req.trace_id
    if not trace_id:
        row = await app.state.deps.records.db.fetchone(
            "select ref->>'trace_id' as trace_id from audit_log where thread_id=%s and ref ? 'trace_id' order by at desc limit 1",
            (thread_id,),
        )
        trace_id = (row or {}).get("trace_id")
    if not trace_id:
        raise HTTPException(404, "no trace id recorded for this thread")
    client = getattr(app.state, "ls_client", None) or build_client(
        settings.langsmith_api_key, settings.langsmith_endpoint, mask=settings.langsmith_mask_pii
    )
    fb = await asyncio.to_thread(
        client.create_feedback,
        run_id=trace_id,
        key=req.key,
        score=req.score,
        comment=req.comment,
        source_info={"thread_id": thread_id},
    )
    return {"feedback_id": str(fb.id), "trace_id": trace_id}


@app.get("/agents", dependencies=[Depends(require_token)])
async def agents() -> dict[str, Any]:
    """Describe the graph, agents, tools and model door (``GET /agents``, 200)."""
    deps = app.state.deps
    return {
        "graph": deps.agents.graph.__dict__,
        "agents": deps.factory.describe(),
        "tools": deps.tools.describe(),
        "model_door": deps.models.describe(),
    }


# producer UI integration layer (docs/design.md §14): read endpoints + manual intake, same token guard
app.include_router(ui_router, dependencies=[Depends(require_token)])


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe (``GET /health``): always 200 with ``ok: true``; no token required."""
    return {"ok": True, "service": "agent"}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe (``GET /ready``): 200 when every gating check passes, else 503; no token required.

    Gating checks: Postgres ping, prompts pinned, tools registered, API token configured. The model door
    configuration, trace-delivery counters, trace masking flag and prompt sources are reported but do not
    gate.
    """
    deps = app.state.deps
    checks = {
        "postgres": await app.state.db.ping(),
        "prompts_pinned": deps.prompts.ready(),
        "tools": bool(deps.tools.names()),
        "model_door_configured": deps.models.configured(),
        "api_token_configured": bool(app.state.settings.agent_api_token),
    }
    ok = all(v for k, v in checks.items() if k != "model_door_configured")
    monitor = getattr(app.state, "trace_monitor", None)
    return JSONResponse(
        {
            "ok": ok,
            "checks": checks,
            "model_door": deps.models.describe()["mode"],
            "revision_id": app.state.settings.revision_id,
            # observability truth, not gating readiness: traces dropped by the SDK (rate limit, network) and
            # whether any prompt came from the local fallback instead of the Hub
            "traces": monitor.snapshot() if monitor is not None else None,
            "trace_masking": bool(getattr(app.state, "ls_client", None))
            and app.state.settings.langsmith_mask_pii,
            "prompts_source": deps.prompts.sources(),
        },
        status_code=200 if ok else 503,
    )
