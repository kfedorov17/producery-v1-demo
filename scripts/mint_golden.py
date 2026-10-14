"""Mint EXACTLY ONE offline golden row from a confirmed online failure (the failure→golden-row loop).

The row carries its source trace id. A trace gets one chance to be new — a second mint of the same trace is
refused.

    python scripts/mint_golden.py --trace <trace_id> --expect-class client_service --expect-outcome send_email:approved:executed \
        [--must-not-contain "$" --tag red-team --note "why it failed"] [--raw]

The sender identity is scrubbed by default (deterministic pseudonym, domain kept, name replaced in the text; phones and
SSN shapes redacted) so client identities never land in git; `--raw` keeps the trace text verbatim (fixtures only).

Engine (when entitled) automates the same step from an issue's evidence ("Add offline examples"); this script is
the manual equivalent. After minting: `python -m producery.evals.run crew_e2e` syncs the dataset and runs the row.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from producery.timeutil import utc_now_iso

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATASET = ROOT / "evals" / "datasets" / "crew_e2e.jsonl"


def main() -> int:
    """Build one golden row from the trace's root email and append it to the crew_e2e dataset.

    Refuses with exit 1 when the trace already minted a row or when the trace's root inputs carry no email.
    The checks attached to the row follow the arguments: `schema_valid` only for a `send_email` outcome, and
    `outcome_equals`, `not_contains`, `injection_ignored` only when their inputs are given. Returns 0 after
    appending.
    """
    p = argparse.ArgumentParser()
    p.add_argument("--trace", required=True)
    p.add_argument("--expect-class", required=True)
    p.add_argument("--expect-outcome", default=None)
    p.add_argument("--decision", default="approve")
    p.add_argument("--must-not-contain", action="append", default=[])
    p.add_argument("--injection-marker", action="append", default=[])
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--note", default="")
    p.add_argument(
        "--raw", action="store_true", help="keep the sender identity verbatim (synthetic fixtures only)"
    )
    a = p.parse_args()

    existing = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any((r.get("metadata") or {}).get("source_trace_id") == a.trace for r in existing):
        print(f"refused: trace {a.trace} already minted a golden row (one chance to be new)")
        return 1

    from langsmith import Client

    from producery.settings import get_settings

    _s = get_settings()
    client = Client(api_key=_s.langsmith_api_key, api_url=_s.langsmith_endpoint)
    run = client.read_run(a.trace)
    email = ((run.inputs or {}).get("email")) or {}
    if not email:
        print("refused: the trace's root inputs carry no email")
        return 1
    email = {
        k: email.get(k) for k in ("from_email", "from_name", "subject", "body", "attachment_document_id")
    }
    if not a.raw:
        from producery.evals.scrub import scrub_email

        email = scrub_email(email, salt=a.trace)
    email["id"] = f"golden-{a.trace[:8]}"
    checks = [{"name": "classification_correct"}, {"name": "compliance_clean"}]
    if (a.expect_outcome or "").startswith("send_email"):
        checks.append({"name": "schema_valid"})  # a draft exists only on the send_email route
    outputs: dict = {"classification": a.expect_class}
    if a.expect_outcome:
        outputs["outcome"] = a.expect_outcome
        checks.append({"name": "outcome_equals"})
    if a.must_not_contain:
        outputs["must_not_contain"] = a.must_not_contain
        checks.append({"name": "not_contains"})
    if a.injection_marker:
        outputs["injection_markers"] = a.injection_marker
        checks.append({"name": "injection_ignored"})
    row = {
        "inputs": {"decision": a.decision, "email": email},
        "outputs": outputs,
        "metadata": {
            "row_id": f"golden-{a.trace[:8]}",
            "checks": checks,
            "tags": sorted({"must-pass", "minted", *a.tag}),
            "split": "minted",
            "source_trace_id": a.trace,
            "minted_at": utc_now_iso(),
            "note": a.note,
            "scrubbed": not a.raw,
        },
    }
    with DATASET.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"minted {row['metadata']['row_id']} from trace {a.trace} → {DATASET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
