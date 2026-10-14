"""Token and cost metric for eval rows.

The agent returns per-model token usage with every turn (`usage` in the run payload, from LangChain's
`UsageMetadataCallbackHandler`). Price per million tokens lives in config/models.yaml `pricing` — list prices,
matched by substring of the model id, with a default — so the metric is a repo-reviewable estimate until the LLM
Gateway reports cost per call. On the interim CLI bridge the input count includes the CLI's own system prompt, so the
estimate is an upper bound there; the gate's ceiling and regression tolerance are set with that in mind.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from producery.evals.datasets import ROOT


@lru_cache(maxsize=1)
def pricing() -> dict[str, dict[str, float]]:
    """Return the `pricing` table of config/models.yaml as floats, keyed by model-id substring; cached."""
    raw = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8")) or {}
    table = raw.get("pricing") or {}
    return {str(k): {kk: float(vv) for kk, vv in (v or {}).items()} for k, v in table.items()}


def price_for(model: str, table: dict[str, dict[str, float]] | None = None) -> dict[str, float]:
    """Return the price entry whose key is the longest case-insensitive substring of the model id.

    Args:
        model: The model id as reported in usage metadata.
        table: A pricing table to search; defaults to `pricing()`.

    Returns:
        The matching entry, else the table's `default`, else zero prices for input and output.
    """
    table = table if table is not None else pricing()
    name = (model or "").lower()
    best = None
    for key in table:
        if key == "default":
            continue
        if key.lower() in name and (best is None or len(key) > len(best)):
            best = key
    return table[best] if best else table.get("default", {"input_per_mtok": 0.0, "output_per_mtok": 0.0})


def estimate_cost_usd(
    usage: dict[str, Any] | None, table: dict[str, dict[str, float]] | None = None
) -> float:
    """Estimate the list-price cost in USD of a usage summary, rounded to four decimals.

    Args:
        usage: The agent's summary, `{"models": {model: {input_tokens, output_tokens, ...}},
            "total_tokens": n}`, or None.
        table: A pricing table; defaults to `pricing()`.
    """
    models = (usage or {}).get("models") or {}
    total = 0.0
    for model, um in models.items():
        p = price_for(model, table)
        total += int(um.get("input_tokens") or 0) / 1e6 * p.get("input_per_mtok", 0.0)
        total += int(um.get("output_tokens") or 0) / 1e6 * p.get("output_per_mtok", 0.0)
    return round(total, 4)


def merge_usage(*usages: dict[str, Any] | None) -> dict[str, Any]:
    """Sum the usage of several runs (a turn and its resume) into one summary.

    Args:
        *usages: Usage summaries in the shape `estimate_cost_usd` accepts; None entries are skipped.

    Returns:
        A summary with per-model input, output and total token counts and the overall `total_tokens`.
    """
    models: dict[str, dict[str, int]] = {}
    for u in usages:
        for model, um in ((u or {}).get("models") or {}).items():
            slot = models.setdefault(model, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
            for k in slot:
                slot[k] += int(um.get(k) or 0)
    return {"models": models, "total_tokens": sum(m["total_tokens"] for m in models.values())}
