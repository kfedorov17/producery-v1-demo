"""Load the suite into LangSmith and run it with `langsmith.evaluate()` against the live stack.

    python -m evals.evaluators.run_suite                       # all rows, experiment suite-<sha>-<hash>
    python -m evals.evaluators.run_suite --rows red-subject-injection,edge-empty-body
    python -m evals.evaluators.run_suite --no-judges           # deterministic checks only (no judge calls)
    python -m evals.evaluators.run_suite --local               # no LangSmith experiment; results to the file only

Run from the repository root with the project venv active and the compose stack up. Results go to
`.eval-results/suite.json` in the same row shape the gate reads (`python -m producery.evals.gate` includes
them) and `.eval-results/suite-summary.md` (the results table by category).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import sys
import time
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from langsmith.utils import LangSmithNotFoundError

from evals.evaluators import deterministic as det
from evals.evaluators.judges import JUDGES
from producery.evals.datasets import git_sha, results_dir
from producery.evals.gate import is_judge_key, p95
from producery.timeutil import utc_now_iso

ROOT = pathlib.Path(__file__).resolve().parents[2]
SUITE = ROOT / "evals" / "data" / "suite.jsonl"
DATASET_NAME = "producery-suite"
RESULTS_DIR = results_dir()

TARGETS = {  # metric key -> (target description, pass rule over the rows that carry the key)
    "classification": ("100 %", 1.0),
    "outcome": ("100 %", 1.0),
    "path": ("100 %", 1.0),
    "structure": ("100 %", 1.0),
    "recipient": ("100 %", 1.0),
    "not_contains": ("100 %", 1.0),
    "compliance_clean": ("100 %", 1.0),
    "cites": ("100 %", 1.0),
    "injection": ("100 %", 1.0),
    "trajectory": ("100 %", 1.0),
    "error": ("100 %", 1.0),
    "mentions": ("100 %", 1.0),
    "fields": ("100 %", 1.0),
    "latency": ("100 %", 1.0),
}
JUDGE_TARGET = ("mean >= 2.6, no 0", 2.6)


def _env_value(name: str) -> str:
    """Read one variable from the repository `.env` (the settings object only exposes declared fields)."""
    env = ROOT / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].split("#")[0].strip()
    return ""


def load_rows(path: pathlib.Path = SUITE) -> list[dict[str, Any]]:
    """Read the suite file, one JSON object per line, and check the row ids are unique.

    Args:
        path: The JSONL file to read.

    Raises:
        ValueError: If two rows share a `row_id`.
    """
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [r["metadata"]["row_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate row_id in suite")
    return rows


def row_hash(row: dict[str, Any]) -> str:
    """Return a stable content hash of a row (inputs, outputs and metadata), used to detect changed rows."""
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def sync_dataset(client: Any, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Create or update the LangSmith dataset so it mirrors the suite file exactly.

    Examples are matched by `metadata.row_id`; an example whose content hash changed is replaced, an example
    the file no longer has is deleted.

    Args:
        client: A `langsmith.Client`.
        rows: The suite rows.

    Returns:
        Counts of created, replaced and removed examples.
    """
    try:
        ds = client.read_dataset(dataset_name=DATASET_NAME)
    except LangSmithNotFoundError:
        ds = client.create_dataset(
            DATASET_NAME, description="producery evaluation suite (evals/data/suite.jsonl)"
        )
    existing = {(ex.metadata or {}).get("row_id"): ex for ex in client.list_examples(dataset_id=ds.id)}
    created = replaced = removed = 0
    for row in rows:
        rid, h = row["metadata"]["row_id"], row_hash(row)
        meta = {**row["metadata"], "row_hash": h}
        ex = existing.pop(rid, None)
        if ex is not None and (ex.metadata or {}).get("row_hash") == h:
            continue
        if ex is not None:
            client.delete_example(ex.id)
            replaced += 1
        else:
            created += 1
        client.create_example(inputs=row["inputs"], outputs=row["outputs"], metadata=meta, dataset_id=ds.id)
    for ex in existing.values():
        client.delete_example(ex.id)
        removed += 1
    return created, replaced, removed


def suite_evaluator(judges: bool) -> Any:
    """Build the one evaluator `evaluate()` calls per row: every applicable deterministic check, then the judges.

    Args:
        judges: Whether to call the LLM judges the row names in `metadata.judges`.

    Returns:
        A `(run, example)` function returning `{"results": [...]}`; a check that raises is recorded as
        `score: None` with the error text, so it counts as an evaluator error rather than a verdict.
    """

    def _evaluate(run: Any, example: Any) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        fns = list(det.applicable(example))
        if judges:
            fns += [JUDGES[name] for name in (getattr(example, "metadata", None) or {}).get("judges") or []]
        for fn in fns:
            try:
                verdict = fn(run, example)
                if verdict is not None:  # a judge with nothing to judge records nothing
                    results.append(verdict)
            except Exception as e:  # one failing check must not erase the row's other verdicts
                results.append(
                    {
                        "key": det.KEY_OF.get(fn, getattr(fn, "__name__", "check")),
                        "score": None,
                        "comment": f"evaluator_error: {type(e).__name__}: {str(e)[:200]}",
                    }
                )
        return {"results": results}

    return _evaluate


def record(
    row: dict[str, Any], scores: list[dict[str, Any]], outputs: dict[str, Any], latency: float
) -> dict[str, Any]:
    """Shape one row's results like `producery.evals.run` does, so the gate reads the file unchanged."""
    det_keys = [s["key"] for s in scores if not s["key"].startswith("judge_")]
    return {
        "row_id": row["metadata"]["row_id"],
        "category": row["metadata"]["category"],
        "tags": row["metadata"]["tags"],
        "scores": {s["key"]: s["score"] for s in scores if s["score"] is not None},
        "errors": [s["key"] for s in scores if s["score"] is None],
        "comments": {s["key"]: s.get("comment", "") for s in scores if s.get("comment")},
        "deterministic_keys": det_keys,
        "trajectory_keys": [k for k in det_keys if k in det.TRAJECTORY_KEYS],
        "latency_s": round(float(outputs.get("latency_s") or latency), 2),
        "tokens": outputs.get("tokens"),
        "cost_usd": outputs.get("cost_usd"),
        "trace_ids": outputs.get("trace_ids") or [],
        "thread_id": outputs.get("thread_id"),
    }


def clean_rows(rows: list[dict[str, Any]]) -> int:
    """Count the rows with no evaluator error and every deterministic check at 1 (judge keys are not checks)."""
    return sum(
        1
        for r in rows
        if not r["errors"] and all(v >= 1 for k, v in r["scores"].items() if not is_judge_key(k))
    )


def judges_pass(rows: list[dict[str, Any]]) -> bool:
    """Apply the judge rule per judge key: mean at least `JUDGE_TARGET[1]` and no row at 0; True when unjudged."""
    by_key: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        for k, v in r["scores"].items():
            if is_judge_key(k):
                by_key[k].append(v)
    return all(statistics.mean(vals) >= JUDGE_TARGET[1] and 0 not in vals for vals in by_key.values())


def summarise(rows: list[dict[str, Any]], experiment: str, urls: dict[str, str]) -> str:
    """Render the results table: metric, target, actual, pass/fail, per category and overall.

    Args:
        rows: The recorded rows.
        experiment: The experiment name.
        urls: Links to put in the header (experiment, dataset), may be empty.
    """
    lines = [f"# Suite results: {experiment}", ""]
    for k, v in urls.items():
        lines.append(f"- {k}: {v}")
    clean = clean_rows(rows)
    errors = sum(len(r["errors"]) for r in rows)
    lines += ["", f"Rows: {len(rows)}; clean: {clean}; evaluator errors: {errors}.", ""]
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    for cat in ("happy", "edge", "failure", "redteam", "all"):
        group = rows if cat == "all" else by_cat.get(cat, [])
        if not group:
            continue
        lines += [
            f"## {cat} ({len(group)} rows)",
            "",
            "| metric | target | actual | pass |",
            "|---|---|---|---|",
        ]
        keys = sorted({k for r in group for k in r["scores"]})
        for key in keys:
            vals = [r["scores"][key] for r in group if key in r["scores"]]
            if key.startswith("judge_"):
                mean = statistics.mean(vals)
                ok = mean >= JUDGE_TARGET[1] and 0 not in vals
                lines.append(
                    f"| `{key}` | {JUDGE_TARGET[0]} | mean {mean:.2f} over {len(vals)}, zeros {vals.count(0)} | {'pass' if ok else 'FAIL'} |"
                )
            else:
                rate = sum(1 for v in vals if v >= 1) / len(vals)
                target, floor = TARGETS.get(key, ("100 %", 1.0))
                lines.append(
                    f"| `{key}` | {target} | {rate:.0%} ({sum(1 for v in vals if v >= 1)}/{len(vals)}) | {'pass' if rate >= floor else 'FAIL'} |"
                )
        p95_s = p95([r["latency_s"] for r in group]) or 0.0
        lines.append(f"| p95 latency | <= 180 s | {p95_s:.0f} s | {'pass' if p95_s <= 180 else 'FAIL'} |")
        bad = [
            r
            for r in group
            if r["errors"] or any(v < 1 for k, v in r["scores"].items() if not is_judge_key(k))
        ]
        if bad:
            lines.append("")
            for r in bad:
                failed = {k: v for k, v in r["scores"].items() if v < 1 and not is_judge_key(k)}
                lines.append(
                    f"- `{r['row_id']}`: {failed} {json.dumps(r['comments'])[:240]}"
                    + (f" errors={r['errors']}" if r["errors"] else "")
                )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    """Parse the arguments, sync the dataset, run the suite, write the results, print the summary.

    Returns:
        0 when every row is clean, 1 otherwise.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--rows", default=None, help="comma-separated row ids to run")
    p.add_argument("--prefix", default=f"suite-{git_sha()}")
    p.add_argument("--local", action="store_true", help="no LangSmith experiment; run the target directly")
    p.add_argument("--no-judges", action="store_true", help="skip the LLM judges")
    a = p.parse_args()
    from producery.evals.targets import crew_target
    from producery.settings import get_settings

    settings = get_settings()
    rows = load_rows()
    if a.rows:
        wanted = set(a.rows.split(","))
        rows = [r for r in rows if r["metadata"]["row_id"] in wanted]
    by_id = {r["metadata"]["row_id"]: r for r in rows}
    evaluator = suite_evaluator(judges=not a.no_judges)
    RESULTS_DIR.mkdir(exist_ok=True)
    recorded: list[dict[str, Any]] = []
    urls: dict[str, str] = {}
    use_ls = not a.local and settings.langsmith_tracing and bool(settings.langsmith_api_key)
    if use_ls:
        from langsmith import Client, evaluate

        client = Client(api_key=settings.langsmith_api_key, api_url=settings.langsmith_endpoint)
        created, replaced, removed = sync_dataset(client, load_rows())
        print(f"dataset {DATASET_NAME}: +{created} ~{replaced} -{removed}")
        data: Any = DATASET_NAME
        if a.rows:
            data = [
                ex
                for ex in client.list_examples(dataset_name=DATASET_NAME)
                if (ex.metadata or {}).get("row_id") in by_id
            ]
        results = evaluate(
            crew_target,
            data=data,
            evaluators=[evaluator],
            experiment_prefix=a.prefix,
            metadata={
                "git_sha": git_sha(),
                "revision_id": settings.revision_id,
                "suite_hash": row_hash({"rows": [row_hash(r) for r in load_rows()]}),
                "judges": not a.no_judges,
            },
            max_concurrency=1,
            client=client,
        )
        experiment = getattr(results, "experiment_name", a.prefix)
        for item in results:
            row = by_id.get((item["example"].metadata or {}).get("row_id"))
            if row is None:
                continue
            scores = [
                {
                    "key": er.key,
                    "score": None if er.score is None else int(er.score),
                    "comment": (er.comment or "")[:300],
                }
                for er in item["evaluation_results"]["results"]
            ]
            run = item.get("run")
            outputs = (run.outputs if run is not None else {}) or {}
            lat = (
                (run.end_time - run.start_time).total_seconds()
                if run is not None and run.end_time and run.start_time
                else 0.0
            )
            recorded.append(record(row, scores, outputs, lat))
        host = settings.langsmith_endpoint.replace("api.", "", 1)
        org = _env_value("LANGSMITH_ORG_ID")
        try:
            ds_id = client.read_dataset(dataset_name=DATASET_NAME).id
            session_id = client.read_project(project_name=experiment).id
            base = f"{host}/o/{org}/datasets/{ds_id}" if org else f"{host}/datasets/{ds_id}"
            urls["dataset"] = base
            urls["experiment"] = f"{base}/compare?selectedSessions={session_id}"
        except Exception as e:  # the links are a convenience; the names are the record
            urls["experiment"] = f"{experiment} (link lookup failed: {type(e).__name__})"
    else:
        experiment = f"{a.prefix}-local"
        for row in rows:
            t0 = time.time()
            outputs = crew_target(row["inputs"])
            run = SimpleNamespace(outputs=outputs)
            example = SimpleNamespace(inputs=row["inputs"], outputs=row["outputs"], metadata=row["metadata"])
            recorded.append(record(row, evaluator(run, example)["results"], outputs, time.time() - t0))
            print(f"  {row['metadata']['row_id']}: {recorded[-1]['scores']}")
    (RESULTS_DIR / "suite.json").write_text(
        json.dumps(
            {
                "dataset": "suite",
                "experiment": experiment,
                "git_sha": git_sha(),
                "recorded_at": utc_now_iso(),
                "partial": bool(a.rows),
                "rows": recorded,
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summary = summarise(recorded, experiment, urls)
    (RESULTS_DIR / "suite-summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0 if clean_rows(recorded) == len(recorded) and judges_pass(recorded) else 1


if __name__ == "__main__":
    sys.exit(main())
