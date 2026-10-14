"""Decide the eval gate from the recorded results.

Pure threshold logic over `.eval-results/*.json` against `config/eval_gates.yaml` and `evals/baseline.json`. The exit
code is the gate. No skip flag exists.

    python -m producery.evals.gate                  # decide; writes .eval-results/gate_report.md
    python -m producery.evals.gate --write-baseline # promote the current (green) results to the baseline
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import yaml

from producery.evals.datasets import ROOT, results_dir

RESULTS_DIR = results_dir()
BASELINE = ROOT / "evals" / "baseline.json"
GATES = ROOT / "config" / "eval_gates.yaml"


def is_judge_key(key: str) -> bool:
    """Return whether a score key is a judge score (`judge` from the harness, `judge_<name>` from the suite)."""
    return key == "judge" or key.startswith("judge_")


def p95(values: list[float]) -> float | None:
    """Return the value at index `int(0.95 * n)` of the sorted values (the slowest row below 20 rows), or None."""
    if not values:
        return None
    lat = sorted(values)
    return lat[min(len(lat) - 1, int(0.95 * len(lat)))]


def _row_pass(row: dict, keys: list[str] | None = None) -> bool:
    return all(
        v >= 1 for k, v in row["scores"].items() if not is_judge_key(k) and (keys is None or k in keys)
    )


def check_rates(rows: list[dict]) -> dict[str, float]:
    """Return the pass rate per deterministic check key across the rows (judge keys are excluded).

    Args:
        rows: Recorded result rows, each with a `scores` mapping of check key to score.

    Returns:
        Check key to the share of rows that scored at least 1 on it.
    """
    totals: dict[str, list[int]] = {}
    for r in rows:
        for k, v in r["scores"].items():
            if is_judge_key(k):
                continue
            totals.setdefault(k, []).append(1 if v >= 1 else 0)
    return {k: sum(v) / len(v) for k, v in totals.items() if v}


def decide(
    rows: list[dict],
    gates: dict[str, Any],
    baseline_rates: dict[str, float] | None,
    baseline_cost: dict[str, Any] | None = None,
) -> list[str]:
    """Apply gates G1 to G9 from `config/eval_gates.yaml` to one dataset's rows.

    Args:
        rows: Recorded result rows (`row_id`, `tags`, `scores`, `deterministic_keys`, `trajectory_keys`, `errors`,
            `latency_s`, `cost_usd`).
        gates: The parsed gate configuration; thresholds are read from `gates["gates"]`.
        baseline_rates: Per-check pass rates of the baseline run, or None to skip the regression gate G4.
        baseline_cost: The baseline dataset entry carrying `mean_cost_usd`, or None to skip G9's regression clause.

    Returns:
        The names of the gates that failed, in evaluation order; an empty list means GREEN. A gate that fails
        with a reason carries it after a colon (for example `G4_regression:classification`).
    """
    failures: list[str] = []
    g = gates["gates"]
    must = [r for r in rows if "must-pass" in r["tags"]]
    if must and sum(_row_pass(r) for r in must) / len(must) < float(g["G1_must_pass"]["rate"]):
        failures.append("G1_must_pass")
    det = [r for r in rows if r["deterministic_keys"]]
    if det and sum(_row_pass(r, r["deterministic_keys"]) for r in det) / len(det) < float(
        g["G2_deterministic"]["floor"]
    ):
        failures.append("G2_deterministic")
    judged = [v for r in rows for k, v in r["scores"].items() if is_judge_key(k)]  # every judge, every key
    if judged and (
        statistics.mean(judged) < float(g["G3_judge"]["mean_floor"])
        or judged.count(0) > int(g["G3_judge"]["zero_allowed"])
    ):
        failures.append("G3_judge")
    if baseline_rates:
        tol = float(g["G4_regression"]["tolerance"])
        current = check_rates(rows)
        regressed = [k for k, base in baseline_rates.items() if k in current and current[k] < base - tol]
        if regressed:
            failures.append("G4_regression:" + ",".join(regressed))
    red = [r for r in rows if "red-team" in r["tags"]]
    if red and sum(_row_pass(r) for r in red) / len(red) < float(g["G5_red_team"]["rate"]):
        failures.append("G5_red_team")
    traj = [r for r in rows if r.get("trajectory_keys")]
    if traj and sum(_row_pass(r, r["trajectory_keys"]) for r in traj) / len(traj) < float(
        g["G6_trajectory"]["rate"]
    ):
        failures.append("G6_trajectory")
    errors = sum(len(r.get("errors") or []) for r in rows)
    if errors > int(g.get("G8_evaluator_errors", {}).get("max", 0)):
        failures.append(f"G8_evaluator_errors:{errors}")
    p95_s = p95([float(r["latency_s"]) for r in rows if r.get("latency_s")])
    if p95_s is not None and p95_s > float(g["G7_latency"]["p95_s"]):
        failures.append("G7_latency")
    g9 = g.get("G9_cost")
    cost = mean_cost(rows)
    if g9 and cost is not None:
        if cost > float(g9["max_mean_usd"]):
            failures.append(f"G9_cost:mean={cost:.3f}>{g9['max_mean_usd']}")
        base_cost = (baseline_cost or {}).get("mean_cost_usd")
        if base_cost and cost > float(base_cost) * (1 + float(g9["regression_pct"]) / 100):
            failures.append(
                f"G9_cost:mean={cost:.3f} vs baseline {float(base_cost):.3f} (+{g9['regression_pct']}% allowed)"
            )
    return failures


def mean_cost(rows: list[dict]) -> float | None:
    """Return the mean `cost_usd` over the rows that carry one, rounded to four places, or None if none do."""
    costs = [float(r["cost_usd"]) for r in rows if r.get("cost_usd") is not None]
    return round(sum(costs) / len(costs), 4) if costs else None


def main() -> int:
    """Decide every result file in the results directory, write `gate_report.md` and return the exit code.

    Returns:
        0 when every dataset is GREEN, 1 otherwise or when no results exist; `--write-baseline` refuses on RED.
    """
    p = argparse.ArgumentParser()
    p.add_argument("--write-baseline", action="store_true")
    p.add_argument("--results", default=str(RESULTS_DIR))
    a = p.parse_args()
    gates = yaml.safe_load(GATES.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    files = sorted(Path(a.results).glob("*.json"))
    files = [f for f in files if f.name != "gate_report.json"]
    if not files:
        print("gate: no results — run producery.evals.run first")
        return 1
    report = ["# Eval gate report", ""]
    all_failures: list[str] = []
    new_baseline: dict[str, Any] = {"datasets": {}}
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "rows" not in data or "dataset" not in data:
            continue  # not an experiment result file (e.g. online.json)
        if data.get("partial"):  # a `--rows` run: never a gate input, never a baseline
            report.append(f"## {data['dataset']} — SKIPPED: partial run ({len(data['rows'])} rows, `--rows`)")
            report.append("")
            print(
                f"gate: {path.name} is a partial (--rows) run and is not decided; re-run the dataset in full"
            )
            continue
        rows = data["rows"]
        base = (baseline.get("datasets") or {}).get(data["dataset"]) or {}
        failures = decide(rows, gates, base.get("check_rates"), base)
        verdict = "GREEN" if not failures else f"RED {failures}"
        report.append(
            f"## {data['dataset']} — {len(rows)} rows — {verdict}  (experiment: {data.get('experiment')})"
        )
        report.append("")
        report.append("| row | tags | scores | latency | tokens | cost (USD) | traces |")
        report.append("|---|---|---|---|---|---|---|")
        for r in rows:
            report.append(
                f"| {r['row_id']} | {','.join(r['tags'])} | {json.dumps(r['scores'])} | {r.get('latency_s')}s | "
                f"{r.get('tokens') if r.get('tokens') is not None else ''} | "
                f"{r.get('cost_usd') if r.get('cost_usd') is not None else ''} | {','.join(r.get('trace_ids') or [])} |"
            )
        if (mc := mean_cost(rows)) is not None:
            report.append("")
            report.append(f"mean cost per row: {mc} USD (list-price estimate from the agent's token usage)")
        report.append("")
        bad = [
            (
                r["row_id"],
                {k: v for k, v in r["scores"].items() if k != "judge" and v < 1},
                r.get("comments", {}),
            )
            for r in rows
            if not _row_pass(r)
        ]
        for rid, scores, comments in bad:
            report.append(f"- {rid}: {scores} {json.dumps(comments)[:300]}")
        report.append("")
        all_failures += [f"{data['dataset']}:{f}" for f in failures]
        new_baseline["datasets"][data["dataset"]] = {
            "experiment": data.get("experiment"),
            "git_sha": data.get("git_sha"),
            "check_rates": check_rates(rows),
            "mean_cost_usd": mean_cost(rows),
            "rows": len(rows),
        }
        print(f"{data['dataset']:12s} {len(rows):3d} rows  {verdict}")
    summary = "GREEN" if not all_failures else f"RED — {all_failures}"
    report.insert(2, f"**Verdict: {summary}**")
    (Path(a.results) / "gate_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\ngate:", summary)
    if a.write_baseline:
        if all_failures:
            print("refusing to write a RED baseline")
            return 1
        BASELINE.write_text(json.dumps(new_baseline, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written: {BASELINE}")
    return 1 if all_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
