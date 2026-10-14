"""The eval target survives a dropped connection to the agent.

Docker Desktop's port proxy resets connections on Windows: if the agent recorded the thread it is polled to
completion, otherwise the request is sent once more.
"""

from __future__ import annotations

import httpx
import pytest

from producery.evals.targets import _post_with_recovery


def _client(handler) -> httpx.Client:
    return httpx.Client(base_url="http://agent", transport=httpx.MockTransport(handler))


def test_dropped_connection_with_no_server_record_retries_the_post_once():
    calls = {"post": 0, "get": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            calls["post"] += 1
            if calls["post"] == 1:
                raise httpx.ReadError("[WinError 10054] connection forcibly closed", request=req)
            return httpx.Response(200, json={"thread_id": "eval:x", "paused": True, "values": {"a": 1}})
        calls["get"] += 1
        return httpx.Response(200, json={"status": "done", "next": [], "values": {}})  # nothing recorded yet

    with _client(handler) as c:
        state = _post_with_recovery(c, "/runs", {"email": {}}, "eval:x")
    assert state["paused"] is True and calls["post"] == 2 and calls["get"] == 1


def test_dropped_connection_with_a_running_thread_polls_instead_of_reposting():
    calls = {"post": 0, "get": 0}
    snaps = [
        {"status": "running", "next": ["respond"], "values": {"path": ["intake"]}},
        {"status": "running", "next": ["respond"], "values": {"path": ["intake"]}},
        {
            "status": "paused",
            "next": ["apply_decision"],
            "paused": True,
            "values": {"path": ["intake", "respond"]},
        },
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            calls["post"] += 1
            raise httpx.ReadError("reset", request=req)
        calls["get"] += 1
        return httpx.Response(200, json=snaps[min(calls["get"] - 1, len(snaps) - 1)])

    with _client(handler) as c:
        state = _post_with_recovery(c, "/runs", {"email": {}}, "eval:y", poll_s=0)
    assert state["status"] == "paused" and calls["post"] == 1  # never re-sent: the first POST had arrived
    assert calls["get"] >= 3


def test_http_errors_still_raise_and_503_is_returned_as_evidence():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "failed", "values": {"outcome": "failed"}})

    with _client(handler) as c:
        assert _post_with_recovery(c, "/runs", {}, "eval:z")["status"] == "failed"

    def bad(req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "email required"})

    with _client(bad) as c, pytest.raises(httpx.HTTPStatusError):
        _post_with_recovery(c, "/runs", {}, "eval:z")
