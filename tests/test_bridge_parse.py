"""The interim bridge's reply parser (bridge/claude_cli_bridge.py): envelope, fenced, bare-arguments (B-F1)."""

import importlib.util
import pathlib

SPEC = importlib.util.spec_from_file_location(
    "claude_cli_bridge", pathlib.Path(__file__).resolve().parent.parent / "bridge" / "claude_cli_bridge.py"
)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bridge)


def test_envelope_reply_parses_tool_calls():
    out = bridge.parse_tool_reply(
        '{"content": "", "tool_calls": [{"name": "Classification", "arguments": {"classification": "prospect"}}]}'
    )
    assert out["tool_calls"] == [{"name": "Classification", "arguments": {"classification": "prospect"}}]


def test_fenced_bare_arguments_become_the_forced_tool_call():
    raw = '```json {"classification": "prospect", "confidence": 0.95, "reason": "moved, needs auto"} ```'
    out = bridge.parse_tool_reply(raw, forced="Classification")
    assert out["tool_calls"][0]["name"] == "Classification"
    assert out["tool_calls"][0]["arguments"]["classification"] == "prospect"


def test_bare_object_without_a_forced_tool_stays_content():
    out = bridge.parse_tool_reply('{"classification": "prospect"}')
    assert out["tool_calls"] == [] and out["content"]


def test_forced_tool_name_from_tool_choice():
    assert bridge._forced_tool({"type": "function", "function": {"name": "DraftResponse"}}) == "DraftResponse"
    assert bridge._forced_tool("auto") is None


def test_literal_newlines_inside_strings_still_parse():
    raw = '{"content": "", "tool_calls": [{"name": "DraftResponse", "arguments": {"subject": "Hi", "body": "Hi Wei,\n\nline two", "owner_brief": "b"}}]}'
    out = bridge.parse_tool_reply(raw)
    assert out["tool_calls"][0]["arguments"]["body"].startswith("Hi Wei,")


def test_unparseable_arguments_never_become_an_empty_tool_call():
    raw = '{"content": "", "tool_calls": [{"name": "DraftResponse", "arguments": {"subject": "Hi", "body": "cut off'
    out = bridge.parse_tool_reply(raw)
    assert out["tool_calls"] == [] and out["content"]
