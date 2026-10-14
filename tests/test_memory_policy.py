"""Memory write policies: three kinds, three namespaces, explicit provenance on every record."""

import pytest
from langgraph.store.memory import InMemoryStore

from producery.memory.store import MemoryStore


@pytest.mark.asyncio
async def test_three_kinds_land_in_their_namespaces_with_provenance():
    store = InMemoryStore()
    mem = MemoryStore(store, "lakeview")  # TTLs apply on the Postgres store; InMemoryStore rejects them
    k1 = await mem.write_semantic(
        "Wei Moreau: policy_number = CA-88214-07 (from [doc:doc-moreau-dec-page])",
        source="prop-1",
        confidence=0.9,
    )
    k2 = await mem.write_episodic(
        "crew:x",
        sender="a@b.example",
        classification="client_service",
        outcome="send_email:approved:executed",
        decision="approve",
        trace_id="t-1",
    )
    k3 = await mem.write_procedural(
        "Owner edited a client_service reply to: shorter, no jargon", origin="human_edit", source="prop-1"
    )
    sem = await store.asearch(mem.ns("semantic"))
    epi = await store.asearch(mem.ns("episodic"))
    pro = await store.asearch(mem.ns("procedural"))
    assert (
        [i.key for i in sem] == [k1]
        and sem[0].value["source"] == "prop-1"
        and sem[0].value["confidence"] == 0.9
    )
    assert (
        [i.key for i in epi] == [k2]
        and epi[0].value["trace_id"] == "t-1"
        and epi[0].value["sender"] == "a@b.example"
    )
    assert [i.key for i in pro] == [k3] and pro[0].value["origin"] == "human_edit"
    # same fact twice → same key (dedupe), never a second record
    assert (
        await mem.write_semantic(
            "Wei Moreau: policy_number = CA-88214-07 (from [doc:doc-moreau-dec-page])",
            source="prop-2",
            confidence=0.9,
        )
        == k1
    )
    assert len(await store.asearch(mem.ns("semantic"))) == 1


@pytest.mark.asyncio
async def test_procedural_requires_a_human_origin():
    mem = MemoryStore(InMemoryStore(), "lakeview")
    with pytest.raises(AssertionError):
        await mem.write_procedural("model-inferred preference", origin="model_guess", source="x")


def test_cited_facts_keeps_only_sentences_that_carry_a_kept_citation():
    """Critical review Q4: an approved reply teaches semantic memory its CITED findings, nothing else."""
    from producery.graphs.nodes import cited_facts

    brief = (
        "Maria's HO-1001 renews 2026-11-01 with premium change +4.2% [v_renewals.x_date]. "
        "The analyst could not find a claim history. "
        "Deductible is $1,000 per [doc:doc-maria-dec-page#2]. "
        "REFUSED: none. Tone: warm."
    )
    facts = cited_facts(brief, ["[v_renewals.x_date]", "[doc:doc-maria-dec-page#2]"])
    assert len(facts) == 2
    assert facts[0].startswith("Maria's HO-1001") and "doc-maria-dec-page#2" in facts[1]
    assert cited_facts(brief, []) == [] and cited_facts("", ["[v_renewals.x_date]"]) == []
    assert len(cited_facts(" ".join(f"Fact {i} [v_x.y]." for i in range(9)), ["v_x.y"], limit=5)) == 5
