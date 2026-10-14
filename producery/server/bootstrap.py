"""Builds the composition root. Used by the FastAPI server and the langgraph.json entry point."""

from __future__ import annotations

import asyncio
import logging

import yaml

from producery.agents.config import AgentsConfig
from producery.agents.factory import AgentFactory
from producery.graphs.deps import CrewDeps
from producery.guards.compliance import ComplianceGate
from producery.guards.heuristics import HeuristicClassifier
from producery.model.door import build_model_access
from producery.prompts.hub import build_prompt_source
from producery.settings import Settings, get_settings
from producery.store.db import Database
from producery.store.records import Records
from producery.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def load_yaml(path):
    """Load a YAML file, returning an empty dict when it is empty.

    Args:
        path: The file to read.
    """
    return yaml.safe_load(open(path, encoding="utf-8")) or {}


async def build_deps(settings: Settings, *, db: Database, tools: ToolRegistry | None = None) -> CrewDeps:
    """Build the crew's dependencies from configuration and validate the agent factory.

    Args:
        settings: Application settings.
        db: The database pool (opened, or opening lazily on first use).
        tools: An existing tool registry, or None to connect to the MCP servers from settings.

    Returns:
        The composition root the graph nodes read from.
    """
    agents = AgentsConfig.load(settings.config_dir / "agents.yaml")
    models = build_model_access(settings)
    prompts = build_prompt_source(settings)
    registry = tools if tools is not None else await ToolRegistry.from_mcp(settings)
    factory = AgentFactory(agents, models, prompts, registry)
    factory.validate()
    retention = load_yaml(settings.config_dir / "retention.yaml")
    deps = CrewDeps(
        settings=settings,
        agents=agents,
        models=models,
        prompts=prompts,
        tools=registry,
        factory=factory,
        records=Records(db),
        memory_ttl_days=dict(retention.get("memory") or {}),
        compliance=ComplianceGate.load(settings.config_dir / "compliance_rules.yaml"),
        heuristics=HeuristicClassifier.load(settings.config_dir / "heuristics.yaml"),
        extraction_fields=load_yaml(settings.config_dir / "extraction_fields.yaml"),
    )
    logger.info(
        "deps_ready model_door=%s tools=%s prompts=%s",
        models.describe()["mode"],
        registry.names(),
        list(prompts.pins()),
    )
    return deps


def embed_fn(settings: Settings):
    """The store index embedding function — the same one langgraph.json points at (producery/memory/embed.py)."""
    from producery.memory.embed import embed_texts

    assert settings.embed_dims == 384, "store index dims must match the embedding model (see langgraph.json)"
    return embed_texts


def build_deps_blocking() -> CrewDeps:
    """For `langgraph.json`: the platform owns checkpointer and store; nodes reach the store through the runtime.

    Safe to call with or without a running event loop (Agent Server may call a factory from async context): the
    build runs on a private loop in a worker thread when a loop is already running. The Database pool opens lazily
    on the loop that first uses it, so nothing here binds to the private loop.
    """
    settings = get_settings()
    db = Database(settings.database_url)

    async def _build() -> CrewDeps:
        return await build_deps(settings, db=db)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_build())
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _build()).result()
