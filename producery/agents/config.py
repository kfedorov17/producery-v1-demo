"""Typed view of config/agents.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AgentDef:
    """One agent entry of config/agents.yaml: identity, prompt, model tier, granted tools and guardrails."""

    name: str
    version: int
    kind: str  # stage | deep_agent | subagent | evaluator
    prompt: str
    model_tier: str
    scope: str
    tools: tuple[str, ...] = ()
    subagents: tuple[str, ...] = ()
    reason: str | None = None
    guardrails: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        """Return the `name@version` stamp used in trace metadata and model-door lookups."""
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class GraphDef:
    """The `graph` section of config/agents.yaml: name, version, HITL node and recursion limit."""

    name: str
    version: int
    hitl_node: str
    recursion_limit: int


@dataclass(frozen=True)
class AgentsConfig:
    """The parsed config/agents.yaml: every agent definition keyed by name plus the graph definition."""

    agents: dict[str, AgentDef]
    graph: GraphDef

    @classmethod
    def load(cls, path: Path) -> AgentsConfig:
        """Parse an agents YAML file into typed definitions.

        Missing identity fields default to the mapping key and version 1; a missing `graph` section falls back
        to `inbound_crew@1` with HITL node `apply_decision` and recursion limit 60.

        Args:
            path: Path to the YAML file (normally config/agents.yaml).

        Returns:
            The loaded configuration.

        Raises:
            ValueError: If an agent lists a subagent that is not defined or whose kind is not `subagent`.
        """
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        agents: dict[str, AgentDef] = {}
        for name, a in (raw.get("agents") or {}).items():
            ident = a.get("identity") or {}
            agents[name] = AgentDef(
                name=str(ident.get("name", name)),
                version=int(ident.get("version", 1)),
                kind=str(a.get("kind", "stage")),
                prompt=str(a["prompt"]),
                model_tier=str(a["model_tier"]),
                scope=" ".join(str(a.get("scope", "")).split()),
                tools=tuple(a.get("tools") or []),
                subagents=tuple(a.get("subagents") or []),
                reason=a.get("reason"),
                guardrails=dict(a.get("guardrails") or {}),
            )
        g = raw.get("graph") or {}
        graph = GraphDef(
            name=str(g.get("name", "inbound_crew")),
            version=int(g.get("version", 1)),
            hitl_node=str(g.get("hitl_node", "apply_decision")),
            recursion_limit=int(g.get("recursion_limit", 60)),
        )
        for a in agents.values():
            for s in a.subagents:
                if s not in agents or agents[s].kind != "subagent":
                    raise ValueError(f"agent {a.name!r} references unknown subagent {s!r}")
        return cls(agents=agents, graph=graph)

    def version_stamp(self) -> dict[str, str]:
        """Return a mapping of agent name to `name@version` identity for trace metadata."""
        return {a.name: a.identity for a in self.agents.values()}
