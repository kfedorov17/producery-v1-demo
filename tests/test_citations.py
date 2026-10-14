"""cited ⊆ retrieved is code, not prose (live tests golden-2982aaee and F-15)."""

from producery.guards.citations import filter_citations, retrieved_ids

CALLS = [
    {"name": "task", "output_ids": ["v_policies", "v_renewals"]},
    {"name": "sql_query", "output_ids": ["v_policies"]},
    {"name": "documents_search", "output_ids": ["doc-moreau-dec-page#0", "doc-moreau-dec-page#2"]},
]


def test_keeps_only_what_the_tools_returned_this_turn():
    retrieved = retrieved_ids(CALLS)
    kept, dropped = filter_citations(
        [
            "v_policies.policy_number · CA-88214-07",
            "[doc:doc-moreau-dec-page#0]",
            "doc:doc-moreau-dec-page#2",
            "[doc:doc-moreau-dec-page]",  # document-level marker of a retrieved chunk → fine
            "[doc:chunk_7731]",  # invented from a worked example
            "[doc:doc-keystone-agreement#7]",  # recalled from memory, not retrieved now
            "v_households.segment",  # view never queried this turn
            "my own memory",
        ],
        retrieved,
    )
    assert kept == [
        "v_policies.policy_number · CA-88214-07",
        "[doc:doc-moreau-dec-page#0]",
        "doc:doc-moreau-dec-page#2",
        "[doc:doc-moreau-dec-page]",
    ]
    assert dropped == [
        "[doc:chunk_7731]",
        "[doc:doc-keystone-agreement#7]",
        "v_households.segment",
        "my own memory",
    ]


def test_nothing_retrieved_means_nothing_cited():
    kept, dropped = filter_citations(["v_renewals.x_date", "[doc:doc-x#1]"], set())
    assert kept == [] and len(dropped) == 2
