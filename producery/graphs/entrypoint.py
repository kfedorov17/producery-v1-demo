"""Expose the compiled graph for LangSmith Deployment / Agent Server (`langgraph.json`).

`graph` is a compiled graph built once at import: the Agent Server loads module-level graph objects at container
startup in an executor thread, whereas a callable is treated as a per-request factory and invoked inside the event
loop, where the composition root's Hub pulls and MCP handshake are blocking calls. The platform injects the
checkpointer and the store at run time, so none is set here. The FastAPI server does not import this module; it
builds the same graph through `build_crew`.
"""

from __future__ import annotations

import asyncio
import sys

from producery.graphs.crew import build_graph
from producery.server.bootstrap import build_deps_blocking

if sys.platform == "win32":  # psycopg async needs the selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_deps = build_deps_blocking()
graph = build_graph(_deps).compile(name=_deps.agents.graph.name)
