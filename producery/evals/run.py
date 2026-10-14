"""Run the evaluation suites.

    python -m producery.evals.run --all                 # every dataset → LangSmith experiments + .eval-results/*.json
    python -m producery.evals.run --suite smoke         # golden subset against the deployed stack (pipeline CD)
    python -m producery.evals.run crew_e2e --prefix ci-abc123

With `LANGSMITH_API_KEY` set and tracing on, the native `langsmith.evaluate()` records experiments (metadata: git
sha, dataset hash, revision, model tiers, prompt pins); otherwise the same evaluators record locally. Result files
are the gate's input; LangSmith holds the same scores.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
from types import SimpleNamespace
from typing import Any

import yaml

from producery.evals.datasets import (
    ROOT,
    content_hash,
    dataset_names,
    git_sha,
    load_rows,
    results_dir,
    sync_dataset,
)
from producery.evals.evaluators import (
    DETERMINISTIC,
    METRIC_KEYS,
    TRAJECTORY_KEYS,
    row_checks_evaluator,
    usage_metrics,
)
from producery.evals.targets import TARGETS
from producery.timeutil import utc_now_iso

RESULTS_DIR = results_dir()  # EVAL_RESULTS_DIR (the gate self-test sets it) reaches it through Settings
PARTIAL = False  # set when `--rows` filters the dataset: the result file is marked and the gate skips it


def _record(row: dict, scores: list[dict], outputs: dict, latency: float) -> dict[str, Any]:
    checks = row["metadata"].get("checks", [])
    det_keys = [s["key"] for s, c in zip(scores, checks, strict=False) if c["name"] in DETERMINISTIC]
    return {
        "row_id": row["metadata"]["row_id"],
        "tags": row["metadata"].get("tags", []),
        "split": row["metadata"].get("split", "base"),
        "scores": {s["key"]: s["score"] for s in scores if s["score"] is not None},
        "errors": [s["key"] for s in scores if s["score"] is None],
        "comments": {s["key"]: s.get("comment", "") for s in scores if s.get("comment")},
        "deterministic_keys": det_keys,
        "trajectory_keys": [k for k in det_keys if k in TRAJECTORY_KEYS],
        "latency_s": round(latency, 2),
        "tokens": outputs.get("tokens"),
        "cost_usd": outputs.get("cost_usd"),
        "trace_ids": outputs.get("trace_ids") or [],
        "thread_id": outputs.get("thread_id"),
        "source_trace_id": row["metadata"].get("source_trace_id"),
    }


def _write(name: str, experiment: str, rows: list[dict], extra: dict[str, Any]) -> pathlib.Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "dataset": name,
                "experiment": experiment,
                "git_sha": git_sha(),
                "recorded_at": utc_now_iso(),
                "partial": PARTIAL,
                **extra,
                "rows": rows,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return path


def run_local(name: str, experiment: str, row_filter: set[str] | None = None) -> list[dict]:
    """Run a dataset through its target and the row checks locally, writing `<results dir>/<name>.json`.

    Args:
        name: The dataset name (a key of `TARGETS`).
        experiment: The experiment label recorded in the result file.
        row_filter: Row ids to run; None runs every row.

    Returns:
        One record per row run, as written to the result file.
    """
    target = TARGETS[name]
    per_row = []
    for row in load_rows(name):
        if row_filter and row["metadata"]["row_id"] not in row_filter:
            continue
        t0 = time.time()
        outputs = target(row["inputs"])
        run = SimpleNamespace(outputs=outputs)
        example = SimpleNamespace(
            inputs=row["inputs"], outputs=row.get("outputs") or {}, metadata=row["metadata"]
        )
        scores = row_checks_evaluator(run, example)["results"]
        per_row.append(_record(row, scores, outputs, time.time() - t0))
    _write(name, experiment, per_row, {"recorder": "local"})
    return per_row


def run_langsmith(name: str, prefix: str, row_filter: set[str] | None = None) -> list[dict]:
    """Sync the dataset to LangSmith, run `langsmith.evaluate()` on it and write the same local result file.

    Metric feedback (`tokens`, `cost_usd`) is left in LangSmith and read from the run outputs instead; an
    evaluator that raised is recorded as `score: None`; an example without a repo row is skipped.

    Args:
        name: The dataset name (a key of `TARGETS`).
        prefix: The experiment prefix; the experiment is named `<prefix>-<name>`.
        row_filter: Row ids to run; None runs the whole dataset.

    Returns:
        One record per row evaluated, as written to the result file.
    """
    from langsmith import Client, evaluate

    from producery.evals.context import eval_context

    ctx = eval_context()
    client = Client(api_key=ctx.settings.langsmith_api_key, api_url=ctx.settings.langsmith_endpoint)
    ds_name, created, replaced, removed = sync_dataset(client, name)
    rows = load_rows(name)
    by_id = {r["metadata"]["row_id"]: r for r in rows}
    print(f"  dataset {ds_name}: +{created} ~{replaced} -{removed} rows")
    data: Any = ds_name
    if row_filter:
        data = [
            ex
            for ex in client.list_examples(dataset_name=ds_name)
            if (ex.metadata or {}).get("row_id") in row_filter
        ]
    results = evaluate(
        TARGETS[name],
        data=data,
        evaluators=[row_checks_evaluator, usage_metrics],
        experiment_prefix=f"{prefix}-{name}",
        metadata={
            "git_sha": git_sha(),
            "dataset_hash": content_hash(rows),
            "revision_id": ctx.settings.revision_id,
            "models": {k: v["model"] for k, v in ctx.models.describe()["tiers"].items()},
            "model_door": ctx.models.describe()["mode"],
            "prompts": ctx.prompts.pins(),
        },
        max_concurrency=1,
        client=client,
    )
    per_row = []
    experiment_name = getattr(results, "experiment_name", f"{prefix}-{name}")
    for item in results:
        example = item["example"]
        row = by_id.get((example.metadata or {}).get("row_id"))
        if (
            row is None
        ):  # an example the repo no longer has (sync removes orphans; this is the belt to that brace)
            print(
                f"  skipping example {example.id}: no row {(example.metadata or {}).get('row_id')!r} in the repo"
            )
            continue
        scores: list[dict[str, Any]] = []
        for er in item["evaluation_results"]["results"]:
            if er.key in METRIC_KEYS:
                continue  # feedback in LangSmith; the record reads them from the run outputs below
            if er.score is None:  # the evaluator raised (e.g. judge timeout): an error, not a verdict of 0
                scores.append(
                    {"key": er.key, "score": None, "comment": f"evaluator_error: {(er.comment or '')[:280]}"}
                )
            else:
                scores.append({"key": er.key, "score": int(er.score), "comment": (er.comment or "")[:300]})
        run = item.get("run")
        outputs = (run.outputs if run is not None else {}) or {}
        latency = (
            (run.end_time - run.start_time).total_seconds()
            if run is not None and run.end_time and run.start_time
            else 0.0
        )
        per_row.append(_record(row, scores, outputs, latency))
    _write(name, experiment_name, per_row, {"recorder": "langsmith", "dataset_name": ds_name})
    print(f"  experiment: {experiment_name}")
    return per_row


def use_langsmith(settings: Any, *, local_flag: bool = False) -> bool:
    """Decide whether experiments are recorded in LangSmith: only when a key exists AND tracing is on.

    A host with LANGSMITH_TRACING=false (the GitHub runner, which has no model door) keeps the key for the
    dataset sync and the prompt check but writes results to the local files only, so its RED-by-design runs
    spend no trace quota.

    Args:
        settings: The settings object; `langsmith_api_key` and `tracing_enabled` are read.
        local_flag: The `--local` switch; True forces the local recorder.
    """
    return bool(settings.langsmith_api_key) and bool(settings.tracing_enabled) and not local_flag


def main() -> int:
    """Run the datasets named on the command line (or all, or the smoke suite) and print a summary of each.

    Returns:
        1 when the smoke suite's clean-row rate is below `smoke.must_pass_rate` in config/eval_gates.yaml,
        else 0.
    """
    p = argparse.ArgumentParser()
    p.add_argument("datasets", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--suite", choices=["smoke"], default=None)
    p.add_argument("--prefix", default=f"run-{git_sha()}")
    p.add_argument("--local", action="store_true", help="skip LangSmith experiments (file recorder only)")
    p.add_argument(
        "--rows", default=None, help="comma-separated row ids to run (e.g. a freshly minted golden row)"
    )
    a = p.parse_args()
    gates = yaml.safe_load((ROOT / "config" / "eval_gates.yaml").read_text(encoding="utf-8"))
    row_filter: set[str] | None = set(a.rows.split(",")) if a.rows else None
    global PARTIAL
    PARTIAL = row_filter is not None
    names = a.datasets
    if a.suite == "smoke":
        row_filter = set(gates["smoke"]["rows"])
        names = (
            [n for n in TARGETS if any(r["metadata"]["row_id"] in row_filter for r in load_rows(n))]
            if (ROOT / "evals" / "datasets").exists()
            else []
        )
        a.prefix = f"smoke-{git_sha()}"
    elif a.all or not names:
        names = [n for n in dataset_names() if n in TARGETS]  # never the `_` self-test fixtures
    from producery.settings import get_settings

    use_ls = use_langsmith(get_settings(), local_flag=a.local)
    exit_code = 0
    for name in names:
        print(f"evaluating {name} ({'langsmith' if use_ls else 'local'}) …")
        rows = run_langsmith(name, a.prefix, row_filter) if use_ls else run_local(name, a.prefix, row_filter)
        clean = sum(
            1
            for r in rows
            if not r.get("errors") and all(v >= 1 for k, v in r["scores"].items() if k != "judge")
        )
        errors = sum(len(r.get("errors") or []) for r in rows)
        print(f"  {clean}/{len(rows)} rows clean" + (f"  ({errors} evaluator errors)" if errors else ""))
        if a.suite == "smoke" and clean < len(rows) * float(gates["smoke"]["must_pass_rate"]):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
