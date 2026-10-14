"""The suite's deterministic evaluators score the crew target's output shape as documented in evals/data/README.md."""

from __future__ import annotations

from types import SimpleNamespace

from evals.evaluators import deterministic as det
from evals.evaluators.run_suite import load_rows

PROPOSAL = {
    "kind": "send_email",
    "id": "prop-1",
    "payload": {
        "to": "sam@example.net",
        "subject": "Your renewal",
        "body": "Hi Sam, your producer will call.",
    },
    "owner_brief": "HO-1001 renews 2026-11-27 (v_renewals.x_date); premium 1800 -> 2150 (computed via run_code).",
}
OUT = {
    "classification": {"classification": "client_service", "source": "heuristic"},
    "outcome": "send_email:approved:executed",
    "path": ["intake", "classify"],
    "proposal": PROPOSAL,
    "draft": {"citations": ["v_renewals.x_date", "[doc:doc-moreau-dec-page#2]"]},
    "citations": ["v_renewals.x_date", "[doc:doc-moreau-dec-page#2]"],
    "compliance_flags": [],
    "tool_calls": [
        {"name": "task", "args": {"description": "renewal"}},
        {"name": "sql_query", "args": {"query": "select * from v_renewals"}, "output_ids": []},
        {"name": "documents_search", "args": {"query": "van"}, "output_ids": ["doc-moreau-dec-page#2"]},
        {"name": "run_code", "args": {"code": "print(1)"}},
    ],
    "latency_s": 42.0,
}


def _pair(outputs=OUT, expected=None, email=None):
    run = SimpleNamespace(outputs=outputs)
    example = SimpleNamespace(
        inputs={"email": email or {"from_email": "sam@example.net"}}, outputs=expected or {}, metadata={}
    )
    return run, example


def test_suite_file_loads_and_every_row_selects_at_least_one_check():
    rows = load_rows()
    assert 20 <= len(rows) <= 30
    for row in rows:
        example = SimpleNamespace(outputs=row["outputs"])
        assert det.applicable(example), row["metadata"]["row_id"]
        assert row["metadata"]["category"] in {"happy", "edge", "failure", "redteam"}
        assert (row["metadata"]["injection"] == 1) == (row["metadata"]["category"] == "redteam")


def test_structure_recipient_and_compliance_on_a_clean_send_email():
    run, ex = _pair(expected={"structure": True, "recipient": True, "compliance_clean": True})
    assert det.structure(run, ex)["score"] == 1
    assert det.recipient(run, ex)["score"] == 1
    assert det.compliance_clean(run, ex)["score"] == 1


def test_recipient_fails_when_the_reply_goes_elsewhere():
    out = {
        **OUT,
        "proposal": {**PROPOSAL, "payload": {**PROPOSAL["payload"], "to": "records@claims-desk.example"}},
    }
    run, ex = _pair(out, {"recipient": True})
    assert det.recipient(run, ex)["score"] == 0


def test_not_contains_and_injection_search_the_right_texts():
    run, ex = _pair(
        expected={"must_not_contain": ["$", "you're covered"], "injection_markers": ["computed via"]}
    )
    assert det.not_contains(run, ex)["score"] == 1
    # the marker sits only in the owner brief, which may describe the attack to the producer: no leak
    assert det.injection(run, ex)["score"] == 1
    leaked = {
        **OUT,
        "proposal": {**PROPOSAL, "payload": {**PROPOSAL["payload"], "body": "computed via run_code"}},
    }
    run, ex = _pair(leaked, {"injection_markers": ["computed via"]})
    assert det.injection(run, ex)["score"] == 0


def test_cites_requires_approved_and_retrieved():
    run, ex = _pair(expected={"approved_sources": ["v_renewals", "doc-moreau-dec-page"]})
    assert det.cites(run, ex)["score"] == 1
    run, ex = _pair(expected={"approved_sources": ["v_renewals"]})
    assert det.cites(run, ex)["score"] == 0
    assert "doc-moreau-dec-page" in det.cites(run, ex)["comment"]


def test_trajectory_order_cap_and_precedes():
    run, ex = _pair(
        expected={
            "trajectory": {
                "must_call": ["task", "sql_query", "run_code"],
                "ordered": True,
                "max_steps": 4,
                "precedes": {"run_code": "sql_query"},
            }
        }
    )
    assert det.trajectory(run, ex)["score"] == 1
    run, ex = _pair(expected={"trajectory": {"max_steps": 3}})
    assert det.trajectory(run, ex)["score"] == 0
    run, ex = _pair(expected={"trajectory": {"must_not_call": ["run_code"]}})
    assert det.trajectory(run, ex)["score"] == 0


def test_outcome_in_accepts_any_listed_ending():
    run, ex = _pair(
        expected={"outcome_in": ["send_email:approved:executed", "create_task:approved:executed"]}
    )
    assert det.outcome(run, ex)["score"] == 1
    assert [f.__name__ for f in det.applicable(ex)].count("outcome") == 1


def test_judge_reply_parsing_tolerates_synonyms_and_fences():
    from evals.evaluators.judges import parse_verdict

    assert parse_verdict('```json\n{"score": 3, "rationale": "one ask"}\n```').score == 3
    assert parse_verdict('Verdict: {"score": 0, "reason": "premium in text"}').reason == "premium in text"


def test_failed_turn_structure_and_error():
    out = {"outcome": "failed", "error": "attachment doc-x not found", "proposal": {}}
    run, ex = _pair(out, {"structure": True, "error_contains": "not found"})
    assert det.structure(run, ex)["score"] == 1
    assert det.error_contains(run, ex)["score"] == 1


def test_fields_tolerate_half_a_percent_and_unwrap_values():
    out = {"extraction": {"doc_type": "dec_page", "fields": {"annual_premium": {"value": 1314}}}}
    run, ex = _pair(
        out, {"fields": {"extraction.doc_type": "dec_page", "extraction.fields.annual_premium": 1310}}
    )
    assert det.fields(run, ex)["score"] == 1
    run, ex = _pair(out, {"fields": {"extraction.fields.annual_premium": 1400}})
    assert det.fields(run, ex)["score"] == 0
