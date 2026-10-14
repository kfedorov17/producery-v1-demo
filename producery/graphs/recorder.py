"""LangChain callback that records every tool call observed under a run (parent + subagents).

This is the trajectory evidence that evals assert on (must_call / must_not_call / args / max_steps). Native
callbacks, no instrumentation inside tools.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

_ID_RE = re.compile(r"doc-[\w\-]+#\d+|v_\w+")


class ToolCallRecorder(BaseCallbackHandler):
    """Callback handler that accumulates one record per tool call in `calls`, in start order."""

    def __init__(self) -> None:
        """Start with an empty call list."""
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Append a `started` record with the tool name and its trimmed arguments.

        Args:
            serialized: The serialized tool; its `name` is used when present.
            input_str: The string input, kept (truncated to 500 chars) when structured `inputs` are absent.
            run_id: The tool run id that later events are matched on.
            inputs: The structured tool inputs, when LangChain provides them.
            **kwargs: Other callback fields; `name` is used as a fallback tool name.
        """
        name = (serialized or {}).get("name") or kwargs.get("name") or "?"
        args = inputs if inputs is not None else {"input": input_str[:500]}
        self.calls.append({"run_id": str(run_id), "name": name, "args": _trim(args), "status": "started"})

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """Mark the matching record `ok` or `error` and store an output preview and the ids found in it.

        An output starting with `{"error"` (a teaching error or `tool_unavailable`) is recorded as `error`.

        Args:
            output: The tool output; its `content` attribute is used when present.
            run_id: The tool run id recorded by `on_tool_start`.
            **kwargs: Ignored.
        """
        for c in self.calls:
            if c["run_id"] == str(run_id):
                full = str(getattr(output, "content", output))
                # a tool that answered with {"error": …} (teaching error or `tool_unavailable`) did not succeed
                c["status"] = "error" if full.lstrip().startswith('{"error"') else "ok"
                c["output_preview"] = full[:200]
                c["output_ids"] = sorted(set(_ID_RE.findall(full)))

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """Mark the matching record `error` and store the error text (truncated to 200 chars).

        Args:
            error: The exception the tool raised.
            run_id: The tool run id recorded by `on_tool_start`.
            **kwargs: Ignored.
        """
        for c in self.calls:
            if c["run_id"] == str(run_id):
                c["status"] = "error"
                c["error"] = str(error)[:200]


def _trim(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in (args or {}).items():
        s = v if isinstance(v, (int, float, bool)) or v is None else str(v)
        out[k] = s[:400] if isinstance(s, str) else s
    return out
