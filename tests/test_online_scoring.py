"""Online scorer calibration.

Idempotent no-op turns are not graded for grounding, an honest "data unavailable" brief is scored by admission,
and a cited draft scores 1.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from producery.evals.datasets import ROOT
from producery.evals.online import score_run
from producery.guards.compliance import ComplianceGate

GATE = ComplianceGate.load(ROOT / "config" / "compliance_rules.yaml")
T0 = datetime(2026, 10, 10, tzinfo=UTC)


def _run(outputs: dict, seconds: float = 5.0):
    return SimpleNamespace(outputs=outputs, start_time=T0, end_time=T0 + timedelta(seconds=seconds))


def test_cited_send_email_turn_is_grounded():
    out = {
        "proposal": {"kind": "send_email", "owner_brief": "HO-1001 renews 2026-11-23 (v_renewals.x_date)"},
        "citations": ["v_renewals.x_date · HO-1001", "[doc:doc-keystone-agreement#7]"],
        "tool_calls": [{"name": "sql_query", "status": "ok"}],
        "draft": {"subject": "Your renewal", "body": "Hi Sam, your producer will walk you through it."},
    }
    s = score_run(_run(out), GATE, slo_s=120)
    assert s["grounding"] == 1.0 and s["compliance_clean"] == 1.0 and s["tool_failure_free"] == 1.0


def test_idempotent_rerun_is_not_graded_for_grounding():
    out = {
        "proposal": {"kind": "send_email", "owner_brief": "x"},
        "outcome": "proposal_already_executed",
        "citations": [],
        "tool_calls": [{"name": "sql_query", "status": "ok"}],
    }
    assert "grounding" not in score_run(_run(out), GATE, slo_s=120)


def test_data_unavailable_brief_is_scored_by_admission():
    out = {
        "proposal": {
            "kind": "send_email",
            "owner_brief": "Policy reference unmatched. Data unavailable — drafted from email alone, flagged.",
        },
        "citations": [],
        "tool_calls": [{"name": "sql_query", "status": "ok"}],
        "draft": {"subject": "Your renewal", "body": "Could you confirm the policy number?"},
    }
    s = score_run(_run(out), GATE, slo_s=120)
    assert s["data_unavailable"] == 1.0 and "grounding" not in s


def test_uncited_draft_with_data_is_ungrounded_and_a_premium_is_flagged():
    out = {
        "proposal": {"kind": "send_email", "owner_brief": "premium moved to 2150"},
        "citations": [],
        "tool_calls": [{"name": "sql_query", "status": "error"}],
        "draft": {"subject": "Renewal", "body": "Your premium will be $2,150 next term."},
    }
    s = score_run(_run(out, seconds=400), GATE, slo_s=120)
    assert s == {"grounding": 0.0, "tool_failure_free": 0.0, "compliance_clean": 0.0, "latency_ok": 0.0}
