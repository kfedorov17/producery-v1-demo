"""The producer-UI integration layer (docs/design.md section 14).

Token-guarded, read-only except the two manual-intake routes, allowlisted book views, connection checklist state.
No database: a fake Records stands in.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import tool

from producery.server.app import app
from producery.tools.registry import ToolRegistry

TOKEN = "test-token"


class FakeDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def fetch_as_role(self, sql, role, *, row_cap, params=None):
        self.calls.append((sql, params))
        assert role == "producery_readonly"
        return [{"household_name": "Sam Patel", "x_date": "2026-11-23"}], False


class FakeRecords:
    def __init__(self) -> None:
        self.db = FakeDB()
        self.emails: list[tuple[dict, str]] = []
        self.docs: list[tuple] = []
        self.audits: list[tuple] = []
        self.connections: dict[str, dict] = {}

    async def list_inbox(self, status, limit):
        return [{"id": "e1", "status": status or "paused", "thread_id": "crew:e1", "subject": "renewal"}]

    async def list_proposals(self, status, limit, thread_class="crew"):
        self.last_thread_class = thread_class
        return [
            {
                "id": "prop-1",
                "thread_id": "crew:e1",
                "kind": "send_email",
                "status": status,
                "owner_brief": "b",
            }
        ]

    async def audit_for_thread(self, thread_id):
        return [{"id": 1, "action": "proposal_staged", "ref": {"trace_id": "t"}}]

    async def list_outbox(self, limit):
        return []

    async def list_documents(self):
        return [{"id": "doc-x", "status": "ready", "chunk_count": 3}]

    async def upsert_email(self, email, status="held"):
        self.emails.append((email, status))

    async def upsert_document(self, doc_id, title, doc_type, body):
        self.docs.append((doc_id, title, doc_type))
        return False  # unchanged content → nothing queued (keeps the test off Redis)

    async def audit(self, thread_id, actor, action, ref):
        self.audits.append((thread_id, actor, action, ref))

    async def get_connections(self):
        return list(self.connections.values())

    async def put_connection(self, provider, status, steps_done):
        self.connections[provider] = {
            "provider": provider,
            "status": status,
            "steps_done": steps_done,
            "updated_at": "now",
        }
        return self.connections[provider]


@tool
def documents_search(query: str, k: int = 5) -> str:
    """Fake search tool."""
    return '{"hits": [{"id": "doc-keystone-agreement#7", "citation": "[doc:doc-keystone-agreement#7]"}], "hit_count": 1}'


@pytest.fixture
def client():
    records = FakeRecords()
    app.state.settings = SimpleNamespace(
        agent_api_token=TOKEN, database_readonly_role="producery_readonly", redis_url="redis://x"
    )
    app.state.deps = SimpleNamespace(
        records=records,
        settings=app.state.settings,
        tools=ToolRegistry({"documents_search": documents_search}),
    )
    c = TestClient(app)
    c.records = records  # type: ignore[attr-defined]
    return c


H = {"authorization": f"Bearer {TOKEN}", "x-producery-user": "sam"}


def test_every_ui_route_requires_the_token(client):
    for path in ("/inbox", "/proposals", "/outbox", "/documents", "/connections", "/book/v_renewals"):
        assert client.get(path).status_code == 401
    assert client.post("/inbox", json={"from_email": "a@b.c", "body": "x"}).status_code == 401


def test_reads_return_rows(client):
    assert client.get("/proposals?status=pending", headers=H).json()["items"][0]["kind"] == "send_email"
    assert client.get("/inbox?status=paused", headers=H).json()["total"] == 1
    assert client.get("/threads/crew:e1/audit", headers=H).json()["items"][0]["action"] == "proposal_staged"
    assert client.get("/documents", headers=H).json()["items"][0]["status"] == "ready"
    assert client.get("/connections", headers=H).json()["items"][0]["provider"] == "email"


def test_book_views_are_allowlisted_and_read_only(client):
    r = client.get("/book/v_renewals?q=sam", headers=H)
    assert r.status_code == 200 and r.json()["items"][0]["household_name"] == "Sam Patel"
    sql, params = client.records.db.calls[-1]
    assert "from v_renewals" in sql and "policy_number::text ilike" in sql and set(params) == {"%sam%"}
    assert client.get("/book/households", headers=H).status_code == 404  # base tables never


def test_document_search_uses_the_analysts_tool(client):
    r = client.get("/documents/search?q=non-compete", headers=H)
    assert r.json()["hits"][0]["citation"] == "[doc:doc-keystone-agreement#7]"


def test_paste_email_lands_as_a_new_inbox_row_with_an_audit(client):
    r = client.post(
        "/inbox",
        json={"from_email": "sam@example.net", "from_name": "Sam", "subject": "hi", "body": "What changed?"},
        headers=H,
    )
    assert r.status_code == 201 and r.json()["thread_id"].startswith("crew:ui-")
    email, status = client.records.emails[0]
    assert status == "new" and email["from_email"] == "sam@example.net"
    assert client.records.audits[-1][1] == "sam" and client.records.audits[-1][2] == "email_pasted"


def test_upload_document_stores_by_hash(client):
    r = client.post(
        "/documents",
        json={
            "title": "Keystone Agreement",
            "doc_type": "contract",
            "text": "1. RELATIONSHIP …",
            "provider": "onedrive",
        },
        headers=H,
    )
    assert (
        r.status_code == 201
        and r.json()["id"].startswith("doc-keystone-agreement-")
        and r.json()["queued"] is False
    )
    assert (
        client.post("/documents", json={"title": "x", "doc_type": "nope", "text": "y"}, headers=H).status_code
        == 422
    )


def test_connections_checklist_persists_and_only_manual_exists(client):
    r = client.put(
        "/connections/onedrive", json={"status": "manual", "steps_done": ["folder", "secret"]}, headers=H
    )
    assert r.status_code == 200 and r.json()["steps_done"] == ["folder", "secret"]
    assert client.get("/connections", headers=H).json()["items"][1]["steps_done"] == ["folder", "secret"]
    assert client.put("/connections/onedrive", json={"status": "connected"}, headers=H).status_code == 409
    assert client.put("/connections/phone", json={}, headers=H).status_code == 404
