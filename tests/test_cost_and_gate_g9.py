"""Critical review Q8/Q14: tokens and cost are measured per row and gated (G9), never scored pass/fail."""

from __future__ import annotations

import yaml

from producery.evals.cost import estimate_cost_usd, merge_usage, price_for
from producery.evals.datasets import ROOT
from producery.evals.evaluators import METRIC_KEYS, usage_metrics
from producery.evals.gate import decide, mean_cost
from producery.server.app import usage_summary

GATES = yaml.safe_load((ROOT / "config" / "eval_gates.yaml").read_text(encoding="utf-8"))
TABLE = {
    "default": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
    "claude-haiku-4-5": {"input_per_mtok": 1.0, "output_per_mtok": 5.0},
    "claude-sonnet-4-6": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
}


def test_usage_summary_and_cost_estimate_follow_the_price_table():
    by_model = {
        "claude-haiku-4-5": {"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000},
        "claude-sonnet-4-6": {"input_tokens": 0, "output_tokens": 100_000, "total_tokens": 100_000},
    }
    usage = usage_summary(by_model)
    assert usage["total_tokens"] == 1_100_000
    assert estimate_cost_usd(usage, TABLE) == 1.0 + 1.5
    assert price_for("anthropic/claude-sonnet-4-6-20260101", TABLE)["input_per_mtok"] == 3.0
    assert price_for("some-unknown-model", TABLE) == TABLE["default"]
    merged = merge_usage(usage, usage, None)
    assert (
        merged["total_tokens"] == 2_200_000
        and merged["models"]["claude-haiku-4-5"]["input_tokens"] == 2_000_000
    )


def test_usage_metrics_evaluator_reports_measurements_and_skips_tool_rows():
    class _Run:
        outputs = {"tokens": 12345, "cost_usd": 0.21}

    res = usage_metrics(_Run(), None)["results"]
    assert {r["key"] for r in res} == METRIC_KEYS
    assert next(r for r in res if r["key"] == "tokens")["score"] == 12345

    class _ToolRun:
        outputs = {"tool": "sql_query", "result": {}}

    assert usage_metrics(_ToolRun(), None)["results"] == []


def _row(rid, cost):
    return {
        "row_id": rid,
        "tags": [],
        "scores": {"classification": 1},
        "deterministic_keys": ["classification"],
        "trajectory_keys": [],
        "latency_s": 1.0,
        "cost_usd": cost,
    }


def test_g9_cost_ceiling_and_regression_against_the_baseline_mean():
    rows = [_row("a", 0.30), _row("b", 0.50)]
    assert mean_cost(rows) == 0.4
    assert decide(rows, GATES, None) == []
    ceiling = float(GATES["gates"]["G9_cost"]["max_mean_usd"])
    assert any(f.startswith("G9_cost") for f in decide([_row("a", ceiling + 1)], GATES, None))
    # +25 % over the baseline mean is allowed, more is a regression
    assert decide(rows, GATES, None, {"mean_cost_usd": 0.33}) == []
    assert any(f.startswith("G9_cost") for f in decide(rows, GATES, None, {"mean_cost_usd": 0.30}))
    # rows without a cost (tool datasets) never trip G9
    assert decide([{**_row("t", None)}], GATES, None, {"mean_cost_usd": 0.01}) == []
