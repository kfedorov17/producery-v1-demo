"""Idempotent LangSmith workspace setup for the project.

Creates the tracing project and the annotation queue, syncs the dataset mirrors, and echoes the
online-evaluator / alert rules that must be created in the UI (no SDK surface for rule creation).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from producery.evals.datasets import dataset_names, sync_dataset  # noqa: E402
from producery.settings import get_settings  # noqa: E402


def main() -> int:
    """Create the project and annotation queue if missing, sync every dataset, then print the UI-only rules.

    A conflict on project creation means it exists and is reported as such; any other error surfaces. The queue is matched by exact name before it is
    created. Returns 0.
    """
    s = get_settings()
    from langsmith import Client
    from langsmith.utils import LangSmithConflictError

    client = Client(api_key=s.langsmith_api_key, api_url=s.langsmith_endpoint)
    cfg = yaml.safe_load((ROOT / "config" / "langsmith" / "online_evals.yaml").read_text(encoding="utf-8"))

    try:
        client.create_project(project_name=s.langsmith_project)
        print(f"project created: {s.langsmith_project}")
    except LangSmithConflictError:
        print(f"project exists: {s.langsmith_project}")

    qname = cfg["annotation_queue"]
    existing = [q for q in client.list_annotation_queues(name=qname) if q.name == qname]
    if existing:
        print(f"annotation queue exists: {qname} ({existing[0].id})")
    else:
        q = client.create_annotation_queue(
            name=qname,
            description="Producery: sampled runs + low online scores; adjudicated failures become golden rows via scripts/mint_golden.py",
            rubric_instructions="Score the client draft 0–3 per the judge rubric; flag any compliance violation; note the fix.",
        )
        print(f"annotation queue created: {qname} ({q.id})")

    for name in dataset_names():
        ds, created, replaced, removed = sync_dataset(client, name)
        print(f"dataset synced: {ds} (+{created} ~{replaced} -{removed})")

    print(
        "\nOnline evaluator rules to create in the UI (Tracing → project → Evaluators) — declared in config/langsmith/online_evals.yaml:"
    )
    for rule in cfg["langsmith_rules"]:
        print(
            f"  - {rule['name']}: {rule['kind']} filter={rule['filter']} "
            + (
                f"prompt={rule.get('prompt')} key={rule.get('feedback_key')}"
                if rule["kind"] == "llm_as_judge"
                else f"action={rule.get('action')} queue={rule.get('queue')}"
            )
        )
    print(
        "SDK-local online metrics and alert thresholds:",
        {k: v["alert_below"] for k, v in cfg["sdk_metrics"].items()},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
