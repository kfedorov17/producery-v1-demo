"""RetrievalFirstMiddleware: a data subagent cannot answer before it retrieves."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from producery.agents.middleware import NUDGE_NAME, UNAVAILABLE, RetrievalFirstMiddleware

MW = RetrievalFirstMiddleware(retrieval_tools=("sql_query", "documents_search"))


def test_first_direct_answer_is_discarded_and_the_model_is_nudged():
    msgs = [
        HumanMessage("what is Wei's policy number?"),
        AIMessage("CAV-10042 (v_policies.policy_number)", id="a1"),
    ]
    out = MW._decide(msgs)
    assert out["jump_to"] == "model"
    assert isinstance(out["messages"][0], RemoveMessage) and out["messages"][0].id == "a1"
    assert out["messages"][1].name == NUDGE_NAME and "sql_query" in out["messages"][1].content


def test_tool_call_first_passes_untouched():
    ai = AIMessage("", tool_calls=[{"name": "sql_query", "args": {"query": "select 1"}, "id": "t1"}], id="a1")
    assert MW._decide([HumanMessage("q"), ai]) is None


def test_final_answer_after_retrieval_passes():
    msgs = [
        HumanMessage("q"),
        AIMessage("", tool_calls=[{"name": "sql_query", "args": {}, "id": "t1"}], id="a1"),
        ToolMessage(content='{"rows": []}', tool_call_id="t1", name="sql_query"),
        AIMessage("brief…", id="a2"),
    ]
    assert MW._decide(msgs) is None


def test_second_refusal_degrades_to_data_unavailable():
    msgs = [
        HumanMessage("q"),
        HumanMessage("nudge", name=NUDGE_NAME),
        AIMessage("still guessing: HO-1 (v_policies)", id="a2"),
    ]
    out = MW._decide(msgs)
    assert "jump_to" not in out
    assert out["messages"][0].id == "a2" and out["messages"][0].content == UNAVAILABLE
