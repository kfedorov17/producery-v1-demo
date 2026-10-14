"""Composition root: every dependency a node needs, built once at startup and injected."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from producery.agents.config import AgentsConfig
from producery.agents.factory import AgentFactory
from producery.guards.compliance import ComplianceGate
from producery.guards.heuristics import HeuristicClassifier
from producery.model.door import ModelAccess
from producery.prompts.hub import PromptSource
from producery.settings import Settings
from producery.store.records import Records
from producery.tools.registry import ToolRegistry


@dataclass
class CrewDeps:
    """Every dependency the crew nodes need, built once at startup and passed to the graph."""

    settings: Settings
    agents: AgentsConfig
    models: ModelAccess
    prompts: PromptSource
    tools: ToolRegistry
    factory: AgentFactory
    records: Records
    memory_ttl_days: dict[str, Any]  # config/retention.yaml → MemoryStore built per call on the runtime store
    compliance: ComplianceGate
    heuristics: HeuristicClassifier
    extraction_fields: dict[str, Any]
    drafter: Any = None  # compiled deep agent, built lazily (needs the model door)
    structured: Any = (
        None  # optional StructuredCaller(agent, schema) -> Runnable; tests inject a fake, prod uses the door
    )

    def trace_metadata(self, thread_id: str, **extra: Any) -> dict[str, Any]:
        """Every trace is stamped at serve time: agent@version, prompt@commit, model tiers, thread, revision."""
        pins = self.prompts.pins()
        return {
            "thread_id": thread_id,
            "graph": f"{self.agents.graph.name}@{self.agents.graph.version}",
            "agents": self.agents.version_stamp(),
            "prompts": {n: f"{self.prompts.get(n).hub_name}:{c}" for n, c in pins.items()},
            "models": {k: v["model"] for k, v in self.models.describe()["tiers"].items()},
            "model_door": self.models.describe()["mode"],
            "model_door_label": self.settings.model_door_label,
            "revision_id": self.settings.revision_id,
            "deployment": self.settings.producery_env,
            "agency_id": self.settings.agency_id,
            **extra,
        }
