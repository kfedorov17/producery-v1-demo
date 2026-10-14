from pathlib import Path

import pytest

from producery.guards.compliance import ComplianceGate
from producery.guards.heuristics import HeuristicClassifier
from producery.ingest.chunker import chunk_document
from producery.mcp_data.sql_guard import SqlGuardError, guard_sql

CONFIG = Path(__file__).resolve().parent.parent / "config"


def test_compliance_gate_flags_every_forbidden_zone():
    gate = ComplianceGate.load(CONFIG / "compliance_rules.yaml")
    codes = {
        f["code"]
        for f in gate.check(
            "Your premium will be $2,150, you're covered, I recommend raising limits, guaranteed savings, SSN 123-45-6789"
        )
    }
    assert {
        "premium_in_client_text",
        "binding_language",
        "coverage_advice",
        "guarantees",
        "sensitive_data",
        "rate_prediction",
    } <= codes


def test_compliance_gate_passes_clean_draft():
    gate = ComplianceGate.load(CONFIG / "compliance_rules.yaml")
    assert (
        gate.check(
            "Hi Sam, thanks for flagging this before the renewal date. Would Tuesday afternoon work for a call?"
        )
        == []
    )


def test_heuristics_carrier_domain_and_keywords():
    h = HeuristicClassifier.load(CONFIG / "heuristics.yaml")
    carrier = h.classify(
        {"from_email": "appointments@harborspecialty.example", "subject": "docs", "body": "upload"}, []
    )
    assert carrier and carrier["classification"] == "carrier_thread"
    billing = h.classify(
        {"from_email": "x@unknown.example", "subject": "commission statement", "body": "net remittance"}, []
    )
    assert billing and billing["classification"] == "billing_commission"
    assert (
        h.classify({"from_email": "new@person.example", "subject": "quote", "body": "I need insurance"}, [])
        is None
    )
    known = h.classify(
        {"from_email": "sam@example.net", "subject": "hi", "body": "question"}, ["sam@example.net"]
    )
    assert known and known["classification"] == "client_service"


@pytest.mark.parametrize(
    "query",
    [
        "select * from households",
        "delete from v_renewals",
        "select 1; select 2",
        "update v_policies set premium=0",
        "select pg_sleep(10) from v_renewals",
    ],
)
def test_sql_guard_rejects_with_teaching_message(query):
    with pytest.raises(SqlGuardError) as e:
        guard_sql(query)
    assert (
        "v_renewals" in str(e.value)
        or "read-only" in str(e.value)
        or "one statement" in str(e.value)
        or "SELECT" in str(e.value)
    )


def test_sql_guard_allows_views_and_ctes():
    assert guard_sql("select * from v_renewals where x_date < current_date + 30").startswith("select")
    assert guard_sql("with r as (select * from v_renewals) select count(*) from r join v_policies p on true")


def test_chunker_splits_on_numbered_headings():
    text = "1. RELATIONSHIP\nalpha " * 1 + "\n" + ("x" * 2500) + "\n2. COMPENSATION\n" + ("y" * 2500)
    chunks = chunk_document(text)
    assert len(chunks) >= 2
    assert chunks[0].section and chunks[0].section.startswith("1 RELATIONSHIP")


def test_heuristics_existing_policy_question_is_client_service_never_prospect():
    """An existing-policy question from a known client is client_service by rule, never prospect.

    The fast-tier model once labelled 'does my homeowners cover it' as prospect; the conservative rule decides this
    deterministically and leaves real prospects to the model.
    """
    h = HeuristicClassifier.load(CONFIG / "heuristics.yaml")
    hit = h.classify(
        {
            "from_email": "ray@newmail.example",
            "subject": "golf cart?",
            "body": "does my homeowners cover a golf cart?",
        },
        [],
    )
    assert hit and hit["classification"] == "client_service" and hit["source"] == "heuristic"
    assert (
        h.classify(
            {
                "from_email": "new@x.example",
                "subject": "quote",
                "body": "I need auto insurance for two cars, can you send a quote?",
            },
            [],
        )
        is None
    )
