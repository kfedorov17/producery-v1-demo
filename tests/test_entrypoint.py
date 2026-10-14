"""The langgraph.json entry point.

A compiled graph built once at import, the shape the Agent Server loads at startup, with the composition root safe
to build with or without a running event loop. No network: the MCP registry is faked, prompts come from the local
source.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest
from langchain_core.tools import tool
from langgraph.pregel import Pregel

from producery.server import bootstrap
from producery.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parent.parent


@tool
def sql_query(query: str) -> str:
    """Fake tool."""
    return "{}"


@tool
def documents_search(query: str) -> str:
    """Fake tool."""
    return "{}"


@tool
def run_code(code: str) -> str:
    """Fake tool."""
    return "{}"


@pytest.fixture
def faked_registry(monkeypatch):
    async def _from_mcp(settings):
        return ToolRegistry(
            {"sql_query": sql_query, "documents_search": documents_search, "run_code": run_code}
        )

    monkeypatch.setattr(ToolRegistry, "from_mcp", classmethod(lambda cls, settings: _from_mcp(settings)))
    sys.modules.pop("producery.graphs.entrypoint", None)
    yield
    sys.modules.pop("producery.graphs.entrypoint", None)


def test_entry_point_is_a_compiled_graph_built_at_import(faked_registry):
    cfg = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    path, attr = cfg["graphs"]["inbound_crew"].split(":")
    mod = importlib.import_module(path.lstrip("./").replace("/", ".").removesuffix(".py"))
    g = getattr(mod, attr)
    assert isinstance(g, Pregel), "a compiled graph object, not a factory: loaded once at server startup"
    assert {"intake", "classify", "respond", "apply_decision", "remember"} <= set(g.nodes)
    assert g.checkpointer is None and g.store is None, (
        "the platform injects persistence; the entry point sets none"
    )


def test_composition_root_builds_inside_a_running_event_loop(faked_registry):
    async def main():
        return bootstrap.build_deps_blocking()

    deps = asyncio.run(main())
    assert deps.agents.graph.name == "inbound_crew" and deps.tools.names() == [
        "documents_search",
        "run_code",
        "sql_query",
    ]
