"""Doctrine checks over the config artifacts.

Least-privilege grants, no model names outside models.yaml, ANCHOR structure in every prompt, explicit reducers.
"""

import re
from pathlib import Path

import yaml

from producery.agents.config import AgentsConfig
from producery.model.door import ModelPolicy
from producery.prompts.hub import load_prompt_file
from producery.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
ANCHOR_HEADINGS = ["# Assignment", "# Non-negotiables", "# Context", "# How", "# Output", "# References"]


def test_agents_config_loads_and_validates_subagents():
    cfg = AgentsConfig.load(ROOT / "config" / "agents.yaml")
    assert set(cfg.agents) == {"classifier", "extractor", "drafter", "analyst", "judge"}
    assert cfg.agents["drafter"].subagents == ("analyst",)
    assert cfg.graph.hitl_node == "apply_decision"


def test_least_privilege_tool_grants():
    cfg = AgentsConfig.load(ROOT / "config" / "agents.yaml")
    assert cfg.agents["drafter"].tools == (), "a drafter gets no data tools"
    granted_run_code = [a.name for a in cfg.agents.values() if "run_code" in a.tools]
    assert granted_run_code == ["analyst"], "only the subagent that needs code execution gets run_code"
    assert set(cfg.agents["analyst"].tools) == {"sql_query", "documents_search", "run_code"}
    for a in cfg.agents.values():
        if a.kind == "subagent":
            assert a.reason in (
                "context_isolation_and_tool_scope",
                "tool_scope_isolation",
                "parallelism",
                "context_isolation",
            )


def test_models_yaml_is_the_only_place_with_model_ids():
    policy = ModelPolicy.load(ROOT / "config" / "models.yaml")
    assert {"fast", "worker", "judge"} <= set(policy.tiers)
    worker_family = policy.tier("worker").model.split("/")[0]
    assert policy.tier("judge").model.split("/")[0] != worker_family, "judge must be cross-family"
    pattern = re.compile(r"claude-[a-z0-9\-]+|gpt-[0-9][\w\-\.]*|gemini-[\w\-\.]+", re.I)
    offenders = []
    for path in (ROOT / "producery").rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"model names outside config/models.yaml: {offenders}"
    assert not list((ROOT / "producery").rglob("*anthropic*")), "no provider SDK modules in app code"


def test_every_prompt_is_anchor_with_worked_and_refusal_examples():
    for path in sorted((ROOT / "prompts").glob("*.yaml")):
        spec = load_prompt_file(path)
        system = spec["system"]
        pos = [system.find(h) for h in ANCHOR_HEADINGS]
        assert all(p >= 0 for p in pos), f"{path.name}: missing ANCHOR heading"
        assert pos == sorted(pos), f"{path.name}: ANCHOR sections out of order (stable prefix first)"
        assert "Worked example" in system and "Refusal" in system, (
            f"{path.name}: needs one worked + one refusal example"
        )
        assert spec["human"] == "{{input}}", "one volatile slot after the stable prefix"


def test_prompt_pins_are_commits_not_latest():
    pins = yaml.safe_load((ROOT / "config" / "prompts.yaml").read_text(encoding="utf-8"))["pins"]
    assert set(pins) == {p.stem for p in (ROOT / "prompts").glob("*.yaml")}
    for name, pin in pins.items():
        assert re.fullmatch(r"[0-9a-f]{8,}", pin["commit"]), (
            f"{name}: pin must be a commit hash, got {pin['commit']!r}"
        )


def test_settings_read_only_env_defaults():
    s = Settings(producery_env="test", _env_file=None)  # defaults, not the local .env
    assert s.model_access_mode == "gateway"
    assert s.database_readonly_role == "producery_readonly"


def test_state_channels_have_explicit_reducers():
    from typing import get_type_hints

    from producery.graphs.state import CrewState

    hints = get_type_hints(CrewState, include_extras=True)
    for name in ("compliance_flags", "citations", "artifacts", "path", "tool_calls"):
        assert getattr(hints[name], "__metadata__", None), f"{name} must declare a reducer"


def test_graph_compiles_and_has_expected_nodes():
    import pytest

    from producery.agents.factory import AgentFactory
    from producery.graphs.crew import build_graph
    from producery.graphs.deps import CrewDeps
    from producery.guards.compliance import ComplianceGate
    from producery.guards.heuristics import HeuristicClassifier
    from producery.model.door import build_model_access
    from producery.prompts.hub import LocalPromptSource
    from producery.tools.registry import ToolRegistry

    s = Settings(producery_env="test", prompts_source="local", _env_file=None)
    cfg = AgentsConfig.load(s.config_dir / "agents.yaml")
    prompts = LocalPromptSource(s)
    door = build_model_access(s)
    deps = CrewDeps(
        s,
        cfg,
        door,
        prompts,
        ToolRegistry.empty(),
        AgentFactory(cfg, door, prompts, ToolRegistry.empty()),
        None,
        None,
        ComplianceGate.load(s.config_dir / "compliance_rules.yaml"),
        HeuristicClassifier.load(s.config_dir / "heuristics.yaml"),
        {},
    )
    graph = build_graph(deps).compile()
    nodes = set(graph.get_graph().nodes)
    assert {
        "classify",
        "respond",
        "extract_document",
        "compliance_gate",
        "stage_proposal",
        "apply_decision",
        "execute",
        "record_rejection",
        "remember",
    } <= nodes
    with pytest.raises(RuntimeError):
        ToolRegistry.empty().resolve(["run_code"], agent="analyst")  # unknown name fails fast
