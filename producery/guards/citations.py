"""Deterministic citation guard: cited ⊆ retrieved, enforced in code (live tests golden-2982aaee / F-15).

A citation is kept only when it points at something a tool returned in THIS turn: a document chunk id that appears
in a tool result, or a column of an allowlisted view that a `sql_query` returned. Everything else — markers copied
from recalled memory, ids invented from a worked example (`[doc:chunk_7731]`), bracket-less variants — is dropped
and reported, never passed to the owner as a source.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_DOC = re.compile(r"doc-[\w\-]+(?:#\d+)?")
_VIEW = re.compile(r"\b(v_\w+)")


def retrieved_ids(tool_calls: Iterable[dict]) -> set[str]:
    """Collect every id in the `output_ids` of the recorded tool calls.

    Args:
        tool_calls: Tool-call records as the recorder produces them.

    Returns:
        The union of their `output_ids`, as strings.
    """
    out: set[str] = set()
    for c in tool_calls:
        for i in c.get("output_ids") or []:
            out.add(str(i))
    return out


def filter_citations(citations: Iterable[str], retrieved: set[str]) -> tuple[list[str], list[str]]:
    """Split citations into those backed by a retrieved id and those that are not.

    Doc citations must match a retrieved chunk (or its document); view citations must name a view that a query
    returned. A citation that is neither a doc nor a view reference is dropped.

    Args:
        citations: The citation strings as the model wrote them.
        retrieved: The ids a tool returned this turn (see `retrieved_ids`).

    Returns:
        `(kept, dropped)`, each in the original order and spelling (stripped of whitespace).
    """
    docs = {i.split("#")[0] for i in retrieved if i.startswith("doc-")}
    chunks = {i for i in retrieved if i.startswith("doc-")}
    views = {i for i in retrieved if i.startswith("v_")}
    kept: list[str] = []
    dropped: list[str] = []
    for raw in citations:
        c = str(raw).strip()
        m = _DOC.search(c)
        if m or "doc:" in c:
            ident = m.group(0) if m else ""
            ok = bool(ident) and (ident in chunks or ident.split("#")[0] in docs)
            (kept if ok else dropped).append(c)
            continue
        v = _VIEW.search(c)
        if v:
            (kept if v.group(1) in views else dropped).append(c)
            continue
        dropped.append(c)
    return kept, dropped
