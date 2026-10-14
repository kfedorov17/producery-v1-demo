"""The gate's handling of judge keys, p95, partial result files and the suite's exit rule."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from evals.evaluators.run_suite import clean_rows, judges_pass
from producery.evals import gate

GATES = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "eval_gates.yaml").read_text())


def _row(row_id, scores, tags=("must-pass",), latency=10.0):
    return {
        "row_id": row_id,
        "tags": list(tags),
        "scores": scores,
        "errors": [],
        "deterministic_keys": [k for k in scores if not gate.is_judge_key(k)],
        "trajectory_keys": [],
        "latency_s": latency,
        "cost_usd": 0.1,
    }


def test_suite_judge_keys_are_judges_for_every_gate():
    rows = [_row("a", {"outcome": 1, "judge_tone": 1}), _row("b", {"outcome": 1, "judge_completeness": 3})]
    failures = gate.decide(rows, GATES, None)
    assert "G3_judge" in failures  # mean 2.0 < 2.6
    assert "G1_must_pass" not in failures  # a judge 1 is not a deterministic failure
    assert "judge_tone" not in gate.check_rates(rows)  # never a baseline rate
    assert (
        gate.decide([_row("c", {"outcome": 1, "judge_tone": 3, "judge_faithfulness": 3})], GATES, None) == []
    )


def test_a_judge_zero_trips_the_floor_under_any_key():
    rows = [_row("a", {"outcome": 1, "judge_tone": 0}), _row("b", {"outcome": 1, "judge_tone": 3})]
    assert "G3_judge" in gate.decide(rows, GATES, None)


def test_p95_is_the_gate_formula():
    assert gate.p95([]) is None
    assert gate.p95([5.0, 1.0, 3.0]) == 5.0
    assert gate.p95(list(range(1, 21))) == 20


def test_partial_result_files_are_skipped(tmp_path, monkeypatch, capsys):
    (tmp_path / "crew_e2e.json").write_text(
        json.dumps({"dataset": "crew_e2e", "partial": True, "rows": [_row("x", {"outcome": 1})]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["gate", "--results", str(tmp_path)])
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "no-baseline.json")
    assert gate.main() == 0
    assert "SKIPPED: partial run" in (tmp_path / "gate_report.md").read_text(encoding="utf-8")
    assert "not decided" in capsys.readouterr().out


def test_suite_exit_rule_counts_judges_separately():
    rows = [_row("a", {"outcome": 1, "judge_tone": 1}), _row("b", {"outcome": 1, "judge_tone": 3})]
    assert clean_rows(rows) == 2  # deterministic checks clean
    assert not judges_pass(rows)  # mean 2.0 below 2.6
    assert judges_pass([_row("c", {"outcome": 1, "judge_tone": 3})])


def test_baseline_keeps_datasets_a_partial_run_did_not_decide(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"datasets": {"tools": {"rows": 10, "check_rates": {"tool_result": 1.0}}}})
    )
    (tmp_path / "crew_e2e.json").write_text(
        json.dumps({"dataset": "crew_e2e", "rows": [_row("x", {"outcome": 1})]}), encoding="utf-8"
    )
    (tmp_path / "tools.json").write_text(
        json.dumps({"dataset": "tools", "partial": True, "rows": [_row("t", {"tool_result": 1})]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["gate", "--results", str(tmp_path), "--write-baseline"])
    monkeypatch.setattr(gate, "BASELINE", baseline)
    assert gate.main() == 0
    written = json.loads(baseline.read_text(encoding="utf-8"))["datasets"]
    assert written["tools"]["rows"] == 10  # untouched by the partial smoke run
    assert written["crew_e2e"]["rows"] == 1


def test_baseline_is_left_alone_when_nothing_was_decided(tmp_path, monkeypatch):
    (tmp_path / "tools.json").write_text(
        json.dumps({"dataset": "tools", "partial": True, "rows": [_row("t", {"tool_result": 1})]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["gate", "--results", str(tmp_path), "--write-baseline"])
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "baseline.json")
    assert gate.main() == 0  # a GREEN smoke run with nothing to refresh is not a failure
    assert not (tmp_path / "baseline.json").exists()
