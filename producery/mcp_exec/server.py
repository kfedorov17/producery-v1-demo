"""Serve the `run_code` tool over MCP.

The model sees only this schema; the handler calls the executor. Model-generated code never runs in this process or in
any process holding credentials.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from producery.logging_setup import setup_logging
from producery.mcp_exec.executors import CodeExecutor, LangSmithSandboxExecutor, SealedProcessExecutor
from producery.settings import Settings, get_settings

logger = logging.getLogger("mcp-exec")
MAX_CODE = 20_000


def _err(code: str, message: str, hint: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "hint": hint}}


def build_server(settings: Settings, executor: CodeExecutor) -> FastMCP:
    """Build the FastMCP server exposing `run_code` and a `/health` route backed by the executor.

    The tool description the model sees is set on the `@mcp.tool` decorator. Empty code and an executor
    exception are returned as structured `{"error": {code, message, hint}}` objects; otherwise the
    executor's result is returned with the executor name added.

    Args:
        settings: Application settings; not read by this function.
        executor: The `CodeExecutor` that runs snippets and answers the health check.

    Returns:
        The configured server; the caller runs it with the streamable-http transport.
    """
    mcp = FastMCP(
        "producery-exec",
        instructions="Sandboxed Python execution. No network, no persistence, standard library only.",
        host="0.0.0.0",  # noqa: S104
        port=8082,
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(
        name="run_code",
        description=(
            "Execute a short Python 3 snippet in an isolated sandbox (no network, no filesystem persistence, "
            "standard library only; 256 MB; wall clock ≤ 60 s). `inputs` is a JSON object exposed to the code as the "
            "variable `inputs`. Print results to stdout (JSON recommended). Files written to ./out/ come back as "
            "artifacts. Returns {stdout, stderr, exit_status, timed_out, duration_ms, artifacts}. A timeout is a "
            "normal result with timed_out=true, not an exception."
        ),
        annotations=ToolAnnotations(
            title="Run code (sandboxed)",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
        meta={
            "tags": ["isolated", "compute", "consequential-compute"],
            "owner": "mcp-exec",
            "executor": executor.name,
        },
    )
    async def run_code(
        code: str = Field(description="Python 3 source. Prints are the output channel.", max_length=MAX_CODE),
        inputs: dict[str, Any] | None = Field(
            default=None, description="JSON object available to the code as `inputs`"
        ),
        timeout_s: int = Field(default=20, ge=1, le=60, description="Wall-clock limit in seconds (1–60)"),
    ) -> dict[str, Any]:
        if not code.strip():
            return _err("empty_code", "code is empty", "Send a Python snippet that prints its result")
        try:
            result = await executor.run(code, inputs or {}, timeout_s)
        except Exception as e:
            logger.warning("executor_unavailable err=%s", e)
            return _err(
                "executor_unavailable",
                str(e)[:200],
                "The sandbox is unreachable; report 'computation unavailable'",
            )
        result["executor"] = executor.name
        return dict(result)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        ok = await executor.healthy()
        return JSONResponse(
            {"ok": ok, "service": "mcp-exec", "executor": executor.name, "tools": ["run_code"]},
            status_code=200 if ok else 503,
        )

    return mcp


def build_executor(settings: Settings) -> CodeExecutor:
    """Return the executor selected by `settings.code_executor`.

    Args:
        settings: Application settings; `"langsmith"` selects `LangSmithSandboxExecutor`, anything else the
            `SealedProcessExecutor` pointed at `settings.executor_url`.
    """
    if settings.code_executor == "langsmith":
        return LangSmithSandboxExecutor(settings.exec_max_timeout_s)
    return SealedProcessExecutor(settings.executor_url, settings.exec_max_timeout_s)


def main() -> None:
    """Start the mcp-exec server on port 8082 with the configured executor."""
    settings = get_settings()
    setup_logging("mcp-exec")
    executor = build_executor(settings)
    logger.info("mcp_exec_started port=8082 executor=%s", executor.name)
    build_server(settings, executor).run(transport="streamable-http")


if __name__ == "__main__":
    main()
