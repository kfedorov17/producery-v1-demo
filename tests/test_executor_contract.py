"""Both CodeExecutor implementations return the same result contract.

The sealed compose executor and the LangSmith Sandboxes executor are exercised against fakes, so the test needs
neither Docker nor a Sandbox entitlement.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import httpx
import pytest

from producery.mcp_exec.executors import LangSmithSandboxExecutor, SealedProcessExecutor

CONTRACT = {"stdout", "stderr", "exit_status", "timed_out", "duration_ms", "artifacts", "truncated"}


@pytest.mark.asyncio
async def test_sealed_executor_contract(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["timeout_s"] <= 60
        return httpx.Response(
            200,
            json={
                "stdout": "4\n",
                "stderr": "",
                "exit_status": 0,
                "timed_out": False,
                "duration_ms": 12,
                "artifacts": [],
                "truncated": False,
            },
        )

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def fake_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    result = await SealedProcessExecutor("http://executor:9000", max_timeout_s=60).run("print(2+2)", {}, 999)
    assert set(result) == CONTRACT and result["stdout"] == "4\n" and result["timed_out"] is False


@pytest.mark.asyncio
async def test_langsmith_sandbox_executor_contract(monkeypatch):
    class FakeResult:
        stdout, stderr, exit_code = "4\n", "", 0

    class FakeSandbox:
        def __enter__(self):
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def run(
            self, cmd: str, timeout: int = 60
        ) -> FakeResult:  # the SDK's signature: run(command, *, timeout=60)
            assert timeout > 0
            return FakeResult()

    class FakeClient:
        def sandbox(self) -> FakeSandbox:
            return FakeSandbox()

    fake_mod = types.ModuleType("langsmith.sandbox")
    fake_mod.SandboxClient = FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langsmith.sandbox", fake_mod)
    result = await LangSmithSandboxExecutor(max_timeout_s=60).run("print(2+2)", {}, 10)
    assert set(result) == CONTRACT and result["stdout"] == "4\n" and result["exit_status"] == 0
