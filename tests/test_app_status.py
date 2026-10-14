"""The agent API's failure predicate: degradation markers are not failures."""

from producery.server.app import _failed


def test_only_a_failed_outcome_is_a_failed_turn():
    degraded_but_paused = {"values": {"error": "classifier unavailable: 503", "outcome": None}}
    assert _failed(degraded_but_paused) is False
    assert _failed({"values": {"outcome": "failed", "error": "drafter unavailable"}}) is True
    assert _failed({"values": {"outcome": "send_email:approved:executed"}}) is False
    assert _failed({"values": {}}) is False
