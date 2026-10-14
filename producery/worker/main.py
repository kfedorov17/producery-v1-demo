"""Run the background jobs: arq over Redis.

Jobs:
  ingest_document(doc_id)   chunk, embed and index a document (semantic search)
  run_crew(email_id)        dispatch an inbox email to the agent server as a background run (202) and poll the
                            inbox status until it pauses, finishes or fails (idempotent: thread id = crew:<email_id>)
  poll_inbox()              cron: claim 'new' emails and enqueue run_crew
  prune_checkpoints()       cron: checkpoint retention per thread class (`config/retention.yaml`); a pruned
                            thread's still-pending proposal is marked `expired` (it can never resume)

Retries are bounded (`MAX_TRIES`, deferred 15 s then 30 s); a job that exhausts its tries is dead-lettered into
`audit_log`. A small `/health` HTTP endpoint runs in-process for liveness probes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

import httpx
import yaml
from arq import Retry, cron
from arq.connections import RedisSettings

from producery.ingest.embedder import FastEmbedEmbedder
from producery.ingest.pipeline import ingest_document as _ingest
from producery.logging_setup import setup_logging
from producery.settings import get_settings
from producery.store.db import Database
from producery.store.records import Records

logger = logging.getLogger("worker")
settings = get_settings()
MAX_TRIES = 3


class TurnFailedError(RuntimeError):
    """The agent recorded a failed turn; raised so arq retries with backoff, then dead-letters."""


async def startup(ctx: dict[str, Any]) -> None:
    """Open the worker's shared resources into the arq context and start the in-process health server.

    Args:
        ctx: The arq context; receives db, records, embedder, http and health_task.
    """
    setup_logging("worker")
    db = Database(settings.database_url, max_size=4)
    await db.open()
    ctx["db"] = db
    ctx["records"] = Records(db)
    ctx["embedder"] = FastEmbedEmbedder(settings.embed_model, settings.embed_dims, settings.fastembed_cache)
    ctx["http"] = httpx.AsyncClient(
        base_url=settings.agent_url,
        timeout=30,
        headers={"authorization": f"Bearer {settings.agent_api_token}"},
    )
    ctx["health_task"] = asyncio.create_task(_health_server(db))
    logger.info("worker_started jobs=ingest_document,run_crew,poll_inbox,prune_checkpoints")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Cancel the health server and close the HTTP client and the database pool.

    Args:
        ctx: The arq context populated by ``startup``.
    """
    ctx["health_task"].cancel()
    await ctx["http"].aclose()
    await ctx["db"].close()


async def _fail(ctx: dict[str, Any], job: str, ref: dict[str, Any], exc: Exception) -> None:
    """Retry through arq's `Retry` (deferred 15s, 30s, ...) until `MAX_TRIES`, then dead-letter the failure.

    The dead letter goes into audit_log so nothing is lost silently. Plain exceptions are NOT retried by arq
    — only `Retry` is. Always raises: `Retry` before the last try, the original exception after
    dead-lettering.
    """
    tries = int(ctx.get("job_try") or 1)
    if tries < MAX_TRIES:
        logger.warning("job_retry job=%s try=%s/%s ref=%s", job, tries, MAX_TRIES, json.dumps(ref)[:200])
        raise Retry(defer=15 * tries) from exc
    await ctx["records"].audit("worker", "worker", "job_dead_letter", {"job": job, "try": tries, **ref})
    logger.error("job_dead_letter job=%s ref=%s", job, json.dumps(ref)[:300])
    raise exc


async def ingest_document(ctx: dict[str, Any], doc_id: str) -> dict[str, Any]:
    """Chunk, embed and index a document; a failure is retried, then dead-lettered, through ``_fail``.

    Args:
        ctx: The arq context.
        doc_id: The document id.

    Returns:
        The ingest result, also written to the audit log as ``document_ingested``.
    """
    try:
        result = await _ingest(ctx["records"], ctx["embedder"], doc_id)
    except Exception as e:
        await _fail(ctx, "ingest_document", {"document_id": doc_id, "error": str(e)[:200]}, e)
        raise  # unreachable: _fail always raises
    await ctx["records"].audit("worker", "worker", "document_ingested", result)
    return result


async def run_crew(ctx: dict[str, Any], email_id: str) -> dict[str, Any]:
    """Dispatch an inbox email to the agent server as a background run and wait for its verdict.

    Posts ``/runs`` with ``wait=false`` (202), then polls the inbox row every 3 s until it is ``paused`` or
    ``done``. A 5xx from the agent, a ``failed`` status or a timeout (``turn_timeout_s`` + 60 s) goes through
    ``_fail`` (retry, then dead-letter).

    Args:
        ctx: The arq context.
        email_id: The inbox row id; the thread id is ``crew:<email_id>``.

    Returns:
        The email id, its final status and the thread id.

    Raises:
        httpx.HTTPStatusError: For a 4xx response from the agent, after a `job_dead_letter` audit row.
    """
    http: httpx.AsyncClient = ctx["http"]
    r = await http.post("/runs", json={"email_id": email_id, "wait": False})
    if r.status_code >= 500:
        await _fail(
            ctx,
            "run_crew",
            {"email_id": email_id, "status": r.status_code, "body": r.text[:200]},
            TurnFailedError(f"agent returned {r.status_code}: {r.text[:200]}"),
        )
    if r.status_code >= 400:  # the agent refused the request outright: no retry will change that
        await ctx["records"].audit(
            "worker",
            "worker",
            "job_dead_letter",
            {"job": "run_crew", "email_id": email_id, "status": r.status_code, "body": r.text[:200]},
        )
    r.raise_for_status()
    thread_id = r.json().get("thread_id", f"crew:{email_id}")
    # the run executes in the agent's background; the inbox status is the agent's verdict
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.turn_timeout_s + 60
    while loop.time() < deadline:
        await asyncio.sleep(3)
        row = await ctx["records"].get_email(email_id)
        status = (row or {}).get("status")
        if status in ("paused", "done"):
            logger.info("crew_dispatched email=%s status=%s thread=%s", email_id, status, thread_id)
            return {"email_id": email_id, "status": status, "thread_id": thread_id}
        if status == "failed":
            await _fail(
                ctx,
                "run_crew",
                {"email_id": email_id, "thread_id": thread_id, "status": "failed"},
                TurnFailedError(f"turn failed for {email_id} (thread {thread_id})"),
            )
    await _fail(
        ctx,
        "run_crew",
        {"email_id": email_id, "thread_id": thread_id, "status": "timeout"},
        TurnFailedError(f"turn for {email_id} did not finish within {settings.turn_timeout_s + 60}s"),
    )
    raise AssertionError("unreachable: _fail always raises")


async def poll_inbox(ctx: dict[str, Any]) -> int:
    """Cron: claim up to five ``new`` inbox rows and enqueue ``run_crew`` for each (job id ``crew:<id>``).

    Args:
        ctx: The arq context.

    Returns:
        The number of emails enqueued.
    """
    rows = await ctx["records"].claim_new_emails(limit=5)
    for row in rows:
        await ctx["redis"].enqueue_job("run_crew", row["id"], _job_id=f"crew:{row['id']}")
    if rows:
        logger.info("inbox_polled enqueued=%s", [r["id"] for r in rows])
    return len(rows)


async def prune_checkpoints(ctx: dict[str, Any]) -> dict[str, int]:
    """Retention per thread class: delete checkpoints of threads whose last activity is older than the TTL.

    Last activity comes from ``audit_log``; deletion goes through the checkpointer API. A still-pending
    proposal on a pruned thread is marked ``expired`` and audited.

    Args:
        ctx: The arq context.

    Returns:
        Deleted thread counts per class plus ``proposals_expired``.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    policy = yaml.safe_load(open(settings.config_dir / "retention.yaml", encoding="utf-8")) or {}
    classes = policy.get("thread_classes") or {}
    default = int(policy.get("default_ttl_days", 7))
    deleted: dict[str, int] = {}
    expired = 0
    db: Database = ctx["db"]
    records: Records = ctx["records"]
    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
        # last activity per thread from OUR tables (audit_log carries every node's writes), never from the
        # checkpointer's internal layout; deletion still goes through the checkpointer API (R12)
        rows = await db.fetch(
            "select thread_id, max(at) as last from audit_log where thread_id like '%:%' group by thread_id"
        )
        now = await db.fetchone("select now() as now")
        assert now is not None
        for row in rows:
            cls = str(row["thread_id"]).split(":", 1)[0]
            ttl = int((classes.get(cls) or {}).get("ttl_days", default))
            if row["last"] and row["last"] < now["now"] - timedelta(days=ttl):
                await saver.adelete_thread(row["thread_id"])
                deleted[cls] = deleted.get(cls, 0) + 1
                # a proposal still pending on a pruned thread can never be decided from its checkpoint
                pid = await records.expire_pending_proposal(
                    row["thread_id"], f"checkpoint pruned after {ttl} days without a decision"
                )
                if pid:
                    expired += 1
                    await records.audit(
                        row["thread_id"], "worker", "proposal_expired", {"proposal_id": pid, "ttl_days": ttl}
                    )
    logger.info("checkpoints_pruned %s proposals_expired=%s", json.dumps(deleted), expired)
    return {**deleted, "proposals_expired": expired}


async def _health_server(db: Database) -> None:
    from http import HTTPStatus

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()
        ok = await db.ping()
        body = json.dumps({"ok": ok, "service": "worker"}).encode()
        status = HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE
        writer.write(
            f"HTTP/1.1 {status.value} {status.phrase}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", 8090)  # noqa: S104
    async with server:
        await server.serve_forever()


class WorkerSettings:
    """arq worker configuration: job functions, cron schedule, lifecycle hooks, Redis and retry limits."""

    functions = [ingest_document, run_crew, prune_checkpoints]
    cron_jobs = [
        cron(poll_inbox, second={0, 15, 30, 45}, run_at_startup=True),
        cron(prune_checkpoints, hour=3, minute=15),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_tries = MAX_TRIES
    retry_jobs = True
    job_timeout = settings.turn_timeout_s + 120
    max_jobs = 4
    health_check_interval = 30
