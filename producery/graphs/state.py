"""Typed crew state with explicit reducers.

Append for lists, keyed merge for dicts, last value for scalars. File content never enters state — documents
and large tool outputs are referenced by id.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer that merges two dicts key by key, the right side winning; None counts as empty.

    Args:
        left: The existing state value.
        right: The update.
    """
    return {**(left or {}), **(right or {})}


class EmailRef(TypedDict, total=False):
    """The inbound email as it enters state: sender, subject, capped body and an attachment document id."""

    id: str
    from_email: str
    from_name: str
    subject: str
    body: str  # capped at intake; attachments are document ids, never bodies
    attachment_document_id: str | None


class CrewState(TypedDict, total=False):
    """The inbound crew's graph state; `Annotated` channels append or merge, the rest keep the last value."""

    email: EmailRef
    memories: dict[str, list[dict[str, Any]]]  # last value — recalled once per run
    classification: dict[str, Any]  # last value
    extraction: dict[str, Any]  # last value
    draft: dict[str, Any]  # last value
    proposal: dict[str, Any]  # last value
    decision: dict[str, Any]  # last value — written by the human via resume
    outcome: str  # last value
    error: str  # last value — honest degradation marker
    compliance_flags: Annotated[list[dict[str, Any]], operator.add]
    citations: Annotated[list[str], operator.add]
    artifacts: Annotated[dict[str, str], merge_dicts]  # name → artifact id
    path: Annotated[list[str], operator.add]  # trajectory: node names in execution order
    tool_calls: Annotated[list[dict[str, Any]], operator.add]  # trajectory: tool calls observed in delegates
    memory_writes: list[dict[str, Any]]  # last value — {kind, text} per record `remember` wrote this turn
