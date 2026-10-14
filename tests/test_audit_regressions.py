"""Regressions for the plausible-but-wrong findings of the 2026-10-14 audit."""

from __future__ import annotations

from pathlib import Path

import pytest

from producery.guards.compliance import ComplianceGate
from producery.guards.heuristics import HeuristicClassifier
from producery.mcp_data.sql_guard import SqlGuardError, guard_sql
from producery.observability.masking import mask_text

CONFIG = Path(__file__).resolve().parent.parent / "config"


@pytest.mark.parametrize(
    "query",
    [
        "select * from v_policies, inbox",
        'select body from "documents"',
        "select * from v_policies as p join (inbox) as i on true",
        "select * from v_policies p, v_renewals r, audit_log a",
        "select * from v_policies where exists (select 1 from audit_log)",
    ],
)
def test_sql_guard_rejects_relations_hidden_by_commas_quotes_and_parentheses(query):
    with pytest.raises(SqlGuardError):
        guard_sql(query)


def test_sql_guard_still_allows_comma_joins_over_views_and_ctes():
    guard_sql(
        "select h.display_name from v_households h, v_policies p where p.household_name = h.display_name"
    )
    guard_sql("with r as (select * from v_renewals) select * from r, v_policies")


def test_compliance_gate_catches_the_audited_misses_and_spares_the_false_positive():
    gate = ComplianceGate.load(CONFIG / "compliance_rules.yaml")
    flagged = {
        "You are all set for the renewal.": "binding_language",
        "I'd recommend raising your deductible.": "coverage_advice",
        "We suggest you increase the limit.": "coverage_advice",
        "We guarantee the best price in town.": "guarantees",
        "The premium is going to be higher.": "rate_prediction",
        "The premium is USD 1,200 this year.": "premium_in_client_text",
        "It comes to 1200 dollars.": "premium_in_client_text",
    }
    for text, code in flagged.items():
        assert code in {f["code"] for f in gate.check(text)}, text
    for text in (
        "I spend 5% more time on claims these days.",
        "You're covered by our answering service after hours.",
    ):
        assert gate.check(text) == [], text


def test_heuristics_do_not_read_a_doi_link_as_a_regulator():
    h = HeuristicClassifier.load(CONFIG / "heuristics.yaml")
    assert (
        h.classify(
            {"from_email": "x@y.example", "subject": "paper", "body": "see https://doi.org/10.1000/xyz"}, []
        )
        is None
    )
    hit = h.classify(
        {"from_email": "x@y.example", "subject": "DOI notice", "body": "The DOI requires a filing."}, []
    )
    assert hit is not None and hit["classification"] == "compliance"


def test_mask_text_redacts_international_phone_numbers():
    assert mask_text("call +44 20 7946 0958 or +1 (555) 123-4567") == "call [phone] or [phone]"
    assert mask_text("policy HO-1001 renews 2026-11-27") == "policy HO-1001 renews 2026-11-27"
