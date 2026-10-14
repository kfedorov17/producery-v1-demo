"""Score sampled production traces and alert on breaches.

Deterministic, SDK-local evaluators over recent root runs. Scores land in LangSmith as feedback on the root run;
breaches of the `config/langsmith/online_evals.yaml` thresholds alert (stdout and exit code 2, mirrored by LangSmith
alert rules); low scorers go to the annotation queue.

    python -m producery.evals.online [--hours 24] [--sample 0.5]
"""

from __future__ import annotations

import argparse
import json
import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import yaml

from producery.evals.datasets import ROOT, results_dir
from producery.guards.compliance import ComplianceGate
from producery.timeutil import utc_now_iso

CFG = ROOT / "config" / "langsmith" / "online_evals.yaml"
_CITE_RE = re.compile(r"^(v_\w+(\.\w+)?|\[doc:[\w\-#]+\])")


def score_run(run: Any, gate: ComplianceGate, slo_s: float) -> dict[str, float]:
    """Compute the deterministic online scores (0.0 or 1.0) for one root run.

    `grounding` (or `data_unavailable` when the owner brief admits it) is scored only for a `send_email`
    proposal that made tool calls and is not an idempotent re-run; `compliance_clean` only when a draft body
    exists; `latency_ok` only when the run has start and end times; `tool_failure_free` always.

    Args:
        run: A LangSmith root run with `outputs`, `start_time` and `end_time`.
        gate: The compliance gate applied to the draft subject and body.
        slo_s: The latency SLO in seconds.

    Returns:
        Score name to value, holding only the scores that apply to this run.
    """
    out = run.outputs or {}
    draft = out.get("draft") or {}
    citations = out.get("citations") or []
    calls = out.get("tool_calls") or []
    scores: dict[str, float] = {}
    outcome = str(out.get("outcome") or "")
    brief = str((out.get("proposal") or {}).get("owner_brief") or "")
    if (
        (out.get("proposal") or {}).get("kind") == "send_email"
        and calls
        and not outcome.startswith(
            "proposal_already"
        )  # an idempotent re-run drafts nothing and cites nothing
    ):
        if "data unavailable" in brief.lower():
            # the drafter is required to say so when the analyst found nothing (prompt non-negotiables); that brief
            # legitimately has no citations — count the admission, do not count it as an ungrounded draft
            scores["data_unavailable"] = 1.0
        else:
            scores["grounding"] = (
                1.0 if citations and all(_CITE_RE.match(str(c).strip()) for c in citations) else 0.0
            )
    scores["tool_failure_free"] = 0.0 if any(c.get("status") == "error" for c in calls) else 1.0
    if draft.get("body"):
        scores["compliance_clean"] = (
            0.0 if gate.check(f"{draft.get('subject', '')}\n{draft.get('body', '')}") else 1.0
        )
    if run.end_time and run.start_time:
        scores["latency_ok"] = 1.0 if (run.end_time - run.start_time).total_seconds() <= slo_s else 0.0
    return scores


def main() -> int:
    """Score a sample of recent crew root runs, record feedback, queue low scorers and report breaches.

    Reads `--hours`, `--sample` and `--dry-run` from the command line (defaults come from the config file).
    With `--dry-run` nothing is written to LangSmith. The summary is printed and saved to
    `.eval-results/online.json`.

    Returns:
        2 when any metric mean is below its `alert_below` threshold, else 0.
    """
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=None)
    p.add_argument("--sample", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    hours = a.hours or float(cfg.get("window_hours", 24))
    sample = a.sample if a.sample is not None else float(cfg.get("sample_rate", 0.5))
    metrics = cfg["sdk_metrics"]
    from langsmith import Client

    from producery.settings import get_settings

    _s = get_settings()
    client = Client(api_key=_s.langsmith_api_key, api_url=_s.langsmith_endpoint)
    gate = ComplianceGate.load(ROOT / "config" / "compliance_rules.yaml")
    runs = list(
        client.list_runs(
            project_name=cfg["project"],
            is_root=True,
            start_time=datetime.now(UTC) - timedelta(hours=hours),
            filter='and(eq(name, "inbound_crew"), and(eq(metadata_key, "thread_class"), eq(metadata_value, "crew")))',
            limit=100,
        )
    )
    rng = random.Random(42)  # noqa: S311 — reproducible sampling, not security
    picked = [r for r in runs if rng.random() < sample]
    print(f"online: {len(runs)} root runs in {hours}h, scoring {len(picked)}")
    totals: dict[str, list[float]] = {}
    delivery = trace_delivery_score(_s.eval_agent_url)
    if delivery is not None and "trace_delivery" in metrics:
        totals["trace_delivery"] = [delivery["score"]]
        print("trace delivery:", json.dumps(delivery))
    low: list[UUID | str] = []
    for run in picked:
        scores = score_run(run, gate, float(metrics["latency_ok"].get("slo_s", 180)))
        for key, val in scores.items():
            totals.setdefault(key, []).append(val)
            if not a.dry_run:
                client.create_feedback(
                    run_id=run.id,
                    key=f"online.{key}",
                    score=val,
                    feedback_source_type="model",
                    source_info={"evaluator": "producery.evals.online"},
                )
        if any(v == 0.0 for v in scores.values()):
            low.append(str(run.id))
    breaches = []
    summary = {}
    for key, spec in metrics.items():
        vals = totals.get(key) or []
        mean = sum(vals) / len(vals) if vals else None
        summary[key] = {"n": len(vals), "mean": mean, "alert_below": spec["alert_below"]}
        if mean is not None and mean < float(spec["alert_below"]):
            breaches.append(f"{key}={mean:.2f}<{spec['alert_below']}")
    if low and not a.dry_run:
        queue = _ensure_queue(client, cfg["annotation_queue"])
        try:
            client.add_runs_to_annotation_queue(queue.id, run_ids=low)
        except Exception as e:
            print("annotation queue add failed:", str(e)[:200])
    print(
        json.dumps({"summary": summary, "low_scoring_runs": low, "breaches": breaches}, indent=2, default=str)
    )
    results_dir().mkdir(exist_ok=True)
    (results_dir() / "online.json").write_text(
        json.dumps(
            {"summary": summary, "low": low, "breaches": breaches, "at": utc_now_iso()},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    if breaches:
        print("ALERT:", "; ".join(breaches))
        return 2
    return 0


def trace_delivery_score(agent_url: str) -> dict[str, Any] | None:
    """Read the agent's `/ready` trace-delivery counter.

    The score is 1.0 when the SDK dropped no batch since the agent started, else 0.0 — the alert that the
    audit rows' trace ids may not exist in LangSmith.

    Args:
        agent_url: Base URL of the agent server.

    Returns:
        The score with the `ingest_failures`, `last_failure_at` and `last_error` fields of the counter, or
        None when the agent is unreachable or reports no trace counter.
    """
    import httpx

    try:
        body = httpx.get(f"{agent_url.rstrip('/')}/ready", timeout=10).json()
    except Exception as e:
        print("trace delivery: agent unreachable:", str(e)[:120])
        return None
    traces = body.get("traces") or {}
    if not traces:
        return None
    return {
        "score": 1.0 if traces.get("delivered", True) else 0.0,
        "ingest_failures": traces.get("ingest_failures"),
        "last_failure_at": traces.get("last_failure_at"),
        "last_error": traces.get("last_error"),
    }


def _ensure_queue(client: Any, name: str) -> Any:
    for q in client.list_annotation_queues(name=name):
        if q.name == name:
            return q
    return client.create_annotation_queue(
        name=name, description="Producery: low online scores and sampled reviews → golden rows"
    )


if __name__ == "__main__":
    raise SystemExit(main())
