"""Structural guards on subagents where prose rules did not hold.

Live test F-07: the analyst answered a cited brief with an invented policy number and never called a tool.

RetrievalFirstMiddleware — a data subagent's FIRST model response must be a tool call. If the model answers
directly before any tool has run, the answer is discarded and the model is nudged once; if it still refuses to
retrieve, the brief is replaced by an honest "data unavailable" line so nothing uncited can masquerade as data.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage, ToolMessage

NUDGE_NAME = "retrieval_first_nudge"
NUDGE = (
    "RULE VIOLATION: you answered before retrieving. Nothing in your reply may come from memory, from the question "
    "or from the worked example. Call {tools} now with the smallest query that answers the question; only then compose "
    "the brief from what the tools returned."
)
UNAVAILABLE = (
    "data unavailable: the analyst performed no retrieval, so no figures or identifiers can be reported. "
    "ids retrieved: none"
)


class RetrievalFirstMiddleware(AgentMiddleware):
    """Reject a first answer that skipped the retrieval tools; after one nudge, degrade honestly."""

    def __init__(self, retrieval_tools: Sequence[str], max_nudges: int = 1) -> None:
        """Configure the guard.

        Args:
            retrieval_tools: Names of the tools whose result counts as retrieval.
            max_nudges: How many times a tool-less first answer is discarded and re-prompted before degrading.
        """
        super().__init__()
        self.retrieval_tools = tuple(retrieval_tools)
        self.max_nudges = max_nudges

    def _decide(self, messages: list[AnyMessage]) -> dict[str, Any] | None:
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        ai = messages[-1]
        if ai.tool_calls:
            return None  # the model is calling a tool — the intended first move
        retrieved = any(
            isinstance(m, ToolMessage) and (m.name in self.retrieval_tools or m.name is None)
            for m in messages
        )
        if retrieved:
            return None  # a final answer after retrieval is fine
        nudges = sum(1 for m in messages if isinstance(m, HumanMessage) and m.name == NUDGE_NAME)
        if nudges < self.max_nudges:
            return {
                "messages": [
                    RemoveMessage(id=ai.id) if ai.id else ai,
                    HumanMessage(
                        content=NUDGE.format(tools=" / ".join(self.retrieval_tools)), name=NUDGE_NAME
                    ),
                ],
                "jump_to": "model",
            }
        return {"messages": [AIMessage(content=UNAVAILABLE, id=ai.id)]}

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:  # type: ignore[override]
        """Apply the retrieval-first decision after each model call.

        Args:
            state: The agent state; only `messages` is read.
            runtime: Unused; present for the middleware hook signature.

        Returns:
            A state update (nudge with `jump_to: model`, or the degraded answer), or None to let the turn
            stand.
        """
        return self._decide(list(state["messages"]))

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:  # type: ignore[override]
        """Async variant of `after_model`; same decision, same arguments and return value."""
        return self._decide(list(state["messages"]))
