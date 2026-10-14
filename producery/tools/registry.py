"""Map tool names to LangChain tools sourced from the MCP servers.

An explicit dict of tool name → LangChain tool, built through `langchain-mcp-adapters`. The agent factory resolves
names from config against this dict; an ungranted tool is a schema the agent never sees, an unknown name fails fast at
startup.

Degradation: a tool service outage is a transport failure, which the adapters raise (as an `ExceptionGroup` out of
the streamable-HTTP client) regardless of `handle_tool_errors`; raised inside the deep agent it would end the whole
turn. Every registered tool is therefore wrapped so a transport failure becomes a structured tool result,
`{"error": {"code": "tool_unavailable", ...}}`, that the analyst prompt already knows how to handle ("data
unavailable: <hint>" and stop). The turn completes and the owner brief says what was unreachable.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from producery.settings import Settings

logger = logging.getLogger(__name__)

KNOWN_TOOLS: dict[str, str] = {"sql_query": "data", "documents_search": "data", "run_code": "exec"}
UNAVAILABLE_CODE = "tool_unavailable"


def unavailable_result(tool_name: str, error: BaseException) -> str:
    """Return the JSON text a transport failure turns into, in the shape of the tools' own teaching errors.

    Args:
        tool_name: The tool that could not be reached.
        error: The transport-level exception.
    """
    detail = str(error).splitlines()[0][:200] if str(error) else type(error).__name__
    return json.dumps(
        {
            "error": {
                "code": UNAVAILABLE_CODE,
                "message": f"{tool_name} is unreachable: {detail}",
                "hint": f"The {tool_name} service is down. Report 'data unavailable: {tool_name} unreachable' and stop; do not guess.",
            }
        }
    )


def degrading(tool: BaseTool) -> BaseTool:
    """Wrap an MCP tool so a transport or session failure is a structured result, never an exception.

    Args:
        tool: The tool as `langchain-mcp-adapters` built it.

    Returns:
        A `StructuredTool` with the same name, description, schema and metadata whose coroutine catches every
        exception and returns `unavailable_result`.
    """

    async def _call(**kwargs: Any) -> Any:
        try:
            return await tool.ainvoke(kwargs)
        except Exception as e:  # ExceptionGroup, httpx errors, McpError: all transport-level here
            logger.warning("tool_unavailable tool=%s err=%s", tool.name, str(e)[:200])
            return unavailable_result(tool.name, e)

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema or {},
        coroutine=_call,
        metadata=tool.metadata,
    )


class ToolRegistry:
    """The explicit name-to-tool map the agent factory resolves grants against."""

    def __init__(self, tools: dict[str, BaseTool]) -> None:
        """Keep a copy of the given tools by name.

        Args:
            tools: Tool name to LangChain tool.
        """
        self._tools = dict(tools)

    @classmethod
    async def from_mcp(cls, settings: Settings) -> ToolRegistry:
        """Connect to both MCP servers, wrap every tool with `degrading` and check the expected names are present.

        Args:
            settings: Supplies the two server URLs and the timeouts.

        Returns:
            A registry holding every tool the servers exposed.

        Raises:
            RuntimeError: If a name in `KNOWN_TOOLS` is missing from what the servers exposed.
        """
        client = MultiServerMCPClient(
            {
                "data": {
                    "transport": "streamable_http",
                    "url": settings.mcp_data_url,
                    "timeout": float(settings.mcp_timeout_s),
                },
                "exec": {
                    "transport": "streamable_http",
                    "url": settings.mcp_exec_url,
                    "timeout": float(settings.exec_max_timeout_s + 15),
                },
            },
            handle_tool_errors=True,  # tool exceptions become structured ToolMessages, never an erroring turn
        )
        tools = await client.get_tools()
        by_name = {t.name: degrading(t) for t in tools}
        missing = [n for n in KNOWN_TOOLS if n not in by_name]
        if missing:
            raise RuntimeError(f"MCP servers did not expose expected tools: {missing}; got {sorted(by_name)}")
        logger.info("tool_registry_ready tools=%s", sorted(by_name))
        return cls(by_name)

    @classmethod
    def empty(cls) -> ToolRegistry:
        """Return a registry with no tools (tests and graph construction without a stack)."""
        return cls({})

    def names(self) -> list[str]:
        """Return the registered tool names, sorted."""
        return sorted(self._tools)

    def resolve(self, names: list[str], *, agent: str) -> list[BaseTool]:
        """Return the tools an agent's grant names, in grant order.

        Args:
            names: The tool names from the agent's `tools:` list in `config/agents.yaml`.
            agent: The agent's name, for the error message.

        Raises:
            RuntimeError: If any name is not registered, so a bad grant fails at startup.
        """
        unknown = [n for n in names if n not in self._tools]
        if unknown:
            raise RuntimeError(f"agent {agent!r} grants unknown tools {unknown}; registry has {self.names()}")
        return [self._tools[n] for n in names]

    def describe(self) -> dict[str, Any]:
        """Return each tool's truncated description and the server it comes from, for `/agents` and `/ready`."""
        return {
            n: {"description": (t.description or "")[:120], "server": KNOWN_TOOLS.get(n, "?")}
            for n, t in self._tools.items()
        }
