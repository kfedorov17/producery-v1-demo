"""Integration layer for the producer UI (docs/design.md §14).

Additive only: read endpoints over the tables the crew already writes, two manual-intake routes that feed the
existing inbox and ingestion paths, and the connection checklist state. Nothing here changes the graph,
prompts, tools, memory or evals. Every route is token-guarded by the router dependency in app.py; the UI's
server-side proxy adds `x-producery-user` for the audit trail.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

THREAD_CLASS = re.compile(r"^(crew|eval|smoke)$")
router = APIRouter(tags=["ui"])

BOOK_VIEWS: dict[str, tuple[tuple[str, ...], str]] = {  # view → (searchable text columns, order by)
    "v_households": (("display_name", "segment"), "display_name"),
    "v_policies": (("household_name", "policy_number", "carrier", "line"), "x_date"),
    "v_renewals": (("household_name", "policy_number", "line"), "x_date"),
    "v_appointments": (("carrier_name", "status"), "carrier_name"),
}
PROVIDERS = ("email", "onedrive", "gdrive")
InboxStatus = Literal["held", "new", "running", "paused", "done", "failed"]
ProposalStatus = Literal["pending", "approved", "edited", "rejected", "executed", "expired"]


def _deps(request: Request) -> Any:
    return request.app.state.deps


def _user(request: Request) -> str:
    return (request.headers.get("x-producery-user") or "api")[:80]


async def _job_queue(request: Request) -> Any:
    """One arq pool per process for the UI's enqueues (opened on first use, closed with the app)."""
    state = request.app.state
    pool = getattr(state, "job_queue", None)
    if pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(_deps(request).settings.redis_url))
        state.job_queue = pool
    return pool


# ----------------------------------------------------------------------------- reads
@router.get("/inbox")
async def list_inbox(request: Request, status: InboxStatus | None = None, limit: int = 50) -> dict[str, Any]:
    """List inbox rows with their proposal (``GET /inbox``, 200).

    Args:
        request: The request (application state).
        status: Inbox status filter, or None for all.
        limit: Maximum rows, clamped to 1..200.
    """
    rows = await _deps(request).records.list_inbox(status, min(max(limit, 1), 200))
    return {"items": rows, "total": len(rows)}


@router.get("/proposals")
async def list_proposals(
    request: Request, status: ProposalStatus | None = "pending", limit: int = 50, thread_class: str = "crew"
) -> dict[str, Any]:
    """List proposals of one thread class with their inbox row (``GET /proposals``, 200).

    Producer scope: only the production thread class — eval/smoke threads stage proposals too (live run
    UI-F1).

    Args:
        request: The request (application state).
        status: Proposal status filter, or None for all.
        limit: Maximum rows, clamped to 1..200.
        thread_class: crew, eval or smoke.

    Raises:
        HTTPException: 422 when ``thread_class`` is not one of the three.
    """
    if not THREAD_CLASS.match(thread_class):
        raise HTTPException(422, "thread_class must be crew, eval or smoke")
    rows = await _deps(request).records.list_proposals(status, min(max(limit, 1), 200), thread_class)
    return {"items": rows, "total": len(rows)}


@router.get("/threads/{thread_id}/audit")
async def thread_audit(request: Request, thread_id: str) -> dict[str, Any]:
    """Return a thread's audit rows (``GET /threads/{thread_id}/audit``, 200).

    Args:
        request: The request (application state).
        thread_id: The thread id.
    """
    rows = await _deps(request).records.audit_for_thread(thread_id)
    return {"items": rows, "total": len(rows)}


@router.get("/outbox")
async def list_outbox(request: Request, limit: int = 50) -> dict[str, Any]:
    """List outbox rows (``GET /outbox``, 200).

    Args:
        request: The request (application state).
        limit: Maximum rows, clamped to 1..200.
    """
    rows = await _deps(request).records.list_outbox(min(max(limit, 1), 200))
    return {"items": rows, "total": len(rows)}


@router.get("/book/{view}")
async def book_view(request: Request, view: str, q: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Rows of one allowlisted view under the read-only role — the UI gets no more than the analyst's tool.

    ``GET /book/{view}`` returns 200 with the rows and a ``truncated`` flag; ``q`` matches any searchable
    column of the view, case-insensitively.

    Args:
        request: The request (application state).
        view: One of ``BOOK_VIEWS``.
        q: Optional search text (first 80 characters used).
        limit: Maximum rows, clamped to 1..200.

    Raises:
        HTTPException: 404 when the view is not allowlisted.
    """
    if view not in BOOK_VIEWS:
        raise HTTPException(404, f"unknown view {view!r}; allowed: {sorted(BOOK_VIEWS)}")
    cols, order = BOOK_VIEWS[view]
    deps = _deps(request)
    limit = min(max(limit, 1), 200)
    if q:  # name OR id OR line — the producer searches the way they think (live run UI-F3)
        where = " or ".join(f"{c}::text ilike %s" for c in cols)
        sql = f"select * from {view} where {where} order by {order} limit {limit}"
        params: tuple[Any, ...] | None = tuple(f"%{q[:80]}%" for _ in cols)
    else:
        sql = f"select * from {view} order by {order} limit {limit}"
        params = None
    rows, truncated = await deps.records.db.fetch_as_role(
        sql, deps.settings.database_readonly_role, row_cap=limit, params=params
    )
    return {"view": view, "items": rows, "total": len(rows), "truncated": truncated}


@router.get("/documents")
async def list_documents(request: Request) -> dict[str, Any]:
    """List document metadata (``GET /documents``, 200).

    Args:
        request: The request (application state).
    """
    rows = await _deps(request).records.list_documents()
    return {"items": rows, "total": len(rows)}


@router.get("/documents/search")
async def search_documents(request: Request, q: str, k: int = 5) -> dict[str, Any]:
    """The same `documents_search` MCP tool the analyst uses, through the registry the agent already holds.

    ``GET /documents/search`` returns the tool's JSON result (200); an unparsable tool reply becomes an
    ``error`` object and a non-dict reply becomes ``{"hits": []}``.

    Args:
        request: The request (application state).
        q: Query text (first 500 characters used).
        k: Number of hits, clamped to 1..10.
    """
    deps = _deps(request)
    tool = deps.tools.resolve(["documents_search"], agent="ui")[0]
    raw = await tool.ainvoke({"query": q[:500], "k": min(max(k, 1), 10)})
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and raw[0].get("type") == "text":
        raw = raw[0].get("text")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        data = {"error": {"code": "tool_error", "message": str(raw)[:300]}}
    return data if isinstance(data, dict) else {"hits": []}


# ----------------------------------------------------------------------------- manual intake (the only writes)
class InboundEmail(BaseModel):
    """Body of ``POST /inbox``: a pasted email (sender address, optional name and subject, body)."""

    from_email: str = Field(min_length=3, max_length=200)
    from_name: str = Field(default="", max_length=120)
    subject: str = Field(default="", max_length=300)
    body: str = Field(min_length=1, max_length=20_000)


@router.post("/inbox", status_code=201)
async def paste_email(request: Request, email: InboundEmail) -> dict[str, Any]:
    """Email manual mode: the pasted message becomes an inbox row with status `new` (``POST /inbox``, 201).

    The worker's poll_inbox picks it up exactly like a mailbox-sensor message. The id is derived from sender,
    subject and body, so pasting the same message twice keeps one row; the paste is audited under the user
    header.

    Args:
        request: The request (application state and ``x-producery-user``).
        email: The pasted email.
    """
    deps = _deps(request)
    digest = hashlib.sha1(f"{email.from_email}|{email.subject}|{email.body}".encode()).hexdigest()[:10]  # noqa: S324
    email_id = f"ui-{digest}"
    await deps.records.upsert_email(
        {
            "id": email_id,
            "from_email": email.from_email,
            "from_name": email.from_name,
            "subject": email.subject,
            "body": email.body,
            "attachment_document_id": None,
        },
        status="new",
    )
    await deps.records.audit(
        f"crew:{email_id}", _user(request), "email_pasted", {"email_id": email_id, "via": "ui"}
    )
    return {"id": email_id, "thread_id": f"crew:{email_id}", "status": "new"}


class UploadedDocument(BaseModel):
    """Body of ``POST /documents``: a document's title, type, full text and the provider it came from."""

    title: str = Field(min_length=1, max_length=200)
    doc_type: Literal["contract", "dec_page", "help", "other"] = "other"
    text: str = Field(min_length=1, max_length=2_000_000)
    provider: Literal["onedrive", "gdrive", "upload"] = "upload"


@router.post("/documents", status_code=201)
async def upload_document(request: Request, doc: UploadedDocument) -> dict[str, Any]:
    """OneDrive / Google Drive manual mode: the UI reads the file (md/txt) and sends its text.

    The document is stored by content hash and queued for the worker's existing ingest_document job; content
    that is already ingested unchanged is not re-queued. ``POST /documents`` returns 201 with the id and the
    ``queued`` flag, and the upload is audited under the user header.

    Args:
        request: The request (application state and ``x-producery-user``).
        doc: The uploaded document.
    """
    deps = _deps(request)
    slug = re.sub(r"[^a-z0-9]+", "-", doc.title.lower()).strip("-")[:40] or "document"
    doc_id = f"doc-{slug}-{hashlib.sha1(doc.text.encode()).hexdigest()[:6]}"  # noqa: S324
    queued = await deps.records.upsert_document(doc_id, doc.title, doc.doc_type, doc.text)
    if queued:
        redis = await _job_queue(request)
        await redis.enqueue_job("ingest_document", doc_id, _job_id=f"ingest:{doc_id}")
    await deps.records.audit(
        "documents",
        _user(request),
        "document_uploaded",
        {"document_id": doc_id, "provider": doc.provider, "queued": queued},
    )
    return {"id": doc_id, "queued": queued}


# ----------------------------------------------------------------------------- connections (checklist state)
class ConnectionUpdate(BaseModel):
    """Body of ``PUT /connections/{provider}``: checklist status and the steps completed so far."""

    status: Literal["manual", "connecting", "connected"] = "manual"
    steps_done: list[str] = Field(default_factory=list, max_length=20)


@router.get("/connections")
async def get_connections(request: Request) -> dict[str, Any]:
    """Return every provider's checklist state (``GET /connections``, 200), ``manual`` when none is stored.

    Args:
        request: The request (application state).
    """
    rows = {r["provider"]: r for r in await _deps(request).records.get_connections()}
    items = [
        rows.get(p) or {"provider": p, "status": "manual", "steps_done": [], "updated_at": None}
        for p in PROVIDERS
    ]
    return {"items": items}


@router.put("/connections/{provider}")
async def put_connection(request: Request, provider: str, body: ConnectionUpdate) -> dict[str, Any]:
    """Store a provider's checklist state (``PUT /connections/{provider}``, 200) and audit the change.

    Args:
        request: The request (application state and ``x-producery-user``).
        provider: One of ``PROVIDERS``.
        body: Status and steps done.

    Raises:
        HTTPException: 404 for an unknown provider; 409 when the status is not ``manual`` (connectors arrive
            in phase 2).
    """
    if provider not in PROVIDERS:
        raise HTTPException(404, f"unknown provider {provider!r}; allowed: {PROVIDERS}")
    if body.status != "manual":
        raise HTTPException(409, "only manual mode exists in this build; connectors arrive in phase 2")
    row = await _deps(request).records.put_connection(provider, body.status, body.steps_done)
    await _deps(request).records.audit(
        "connections",
        _user(request),
        "connection_updated",
        {"provider": provider, "steps_done": body.steps_done},
    )
    return row
