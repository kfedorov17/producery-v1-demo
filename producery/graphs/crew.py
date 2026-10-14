"""Assemble the inbound crew graph.

`build_crew` is used by the FastAPI agent server, which injects the checkpointer and the store;
`producery/graphs/entrypoint.py` compiles the same graph once at import for LangSmith Deployment / Agent Server,
which injects its own checkpointer and store at runtime.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from producery.graphs.deps import CrewDeps
from producery.graphs.nodes import CrewNodes
from producery.graphs.state import CrewState


def build_graph(deps: CrewDeps) -> StateGraph:
    """Wire the thirteen nodes and the two conditional routes of the inbound crew into a `StateGraph`.

    Args:
        deps: The composition root; `deps.agents.graph.hitl_node` must name `apply_decision`, the interrupting node.

    Returns:
        The uncompiled graph over `CrewState`.

    Raises:
        ValueError: If `config/agents.yaml` names a different node as the human-in-the-loop node.
    """
    n = CrewNodes(deps)
    hitl = deps.agents.graph.hitl_node
    if (
        hitl != "apply_decision"
    ):  # the interrupt lives in apply_decision; config names it so a rename is caught
        raise ValueError(
            f"config/agents.yaml graph.hitl_node={hitl!r} but the interrupting node is apply_decision"
        )
    g = StateGraph(CrewState)
    g.add_node("intake", n.intake)
    g.add_node("recall_memory", n.recall_memory)
    g.add_node("classify", n.classify)
    g.add_node("extract_document", n.extract_document)
    g.add_node("respond", n.respond)
    g.add_node("task_proposal", n.task_proposal)
    g.add_node("archive", n.archive)
    g.add_node("compliance_gate", n.compliance_gate)
    g.add_node("stage_proposal", n.stage_proposal)
    g.add_node("apply_decision", n.apply_decision)
    g.add_node("execute", n.execute)
    g.add_node("record_rejection", n.record_rejection)
    g.add_node("remember", n.remember)

    g.add_edge(START, "intake")
    g.add_edge("intake", "recall_memory")
    g.add_edge("recall_memory", "classify")
    g.add_conditional_edges(
        "classify",
        n.route_after_classify,
        {
            "extract_document": "extract_document",
            "respond": "respond",
            "task_proposal": "task_proposal",
            "archive": "archive",
        },
    )
    for stage in ("extract_document", "respond", "task_proposal"):
        g.add_edge(stage, "compliance_gate")
    g.add_edge("archive", "remember")
    g.add_edge("compliance_gate", "stage_proposal")
    g.add_edge("stage_proposal", "apply_decision")
    g.add_conditional_edges(
        "apply_decision",
        n.route_after_decision,
        {"execute": "execute", "record_rejection": "record_rejection", "remember": "remember"},
    )
    g.add_edge("execute", "remember")
    g.add_edge("record_rejection", "remember")
    g.add_edge("remember", END)
    return g


def build_crew(deps: CrewDeps, checkpointer: Any, store: Any) -> Any:
    """Compile the inbound crew with the checkpointer and store the host runtime provides.

    Args:
        deps: The composition root built at startup.
        checkpointer: The LangGraph checkpointer (an `AsyncPostgresSaver` under FastAPI).
        store: The LangGraph store that holds long-term memory (an `AsyncPostgresStore` under FastAPI).

    Returns:
        The compiled graph, named after `graph.name` in `config/agents.yaml`.
    """
    return build_graph(deps).compile(checkpointer=checkpointer, store=store, name=deps.agents.graph.name)
