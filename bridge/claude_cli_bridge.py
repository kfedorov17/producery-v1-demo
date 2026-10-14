"""Claude CLI bridge — the FLAGGED interim model door (docs/design.md section 9).

Runs on the host next to a logged-in Claude Code CLI and exposes the OpenAI chat-completions wire shape the
app's Gateway client already speaks. The app does not change: `LANGSMITH_GATEWAY_BASE_URL` points here instead
of at the LangSmith LLM Gateway. Nothing in this file is imported by the application.

Why it exists: the LangSmith LLM Gateway is not enabled on this org and no provider API key exists; the Claude
Code subscription login is the only model access on this machine. Deviations it carries (recorded in the docs):
a custom adapter outside the app, tool calling emulated through a JSON contract in the system prompt, token
usage reported by the CLI, no cross-family judge.

    python bridge/claude_cli_bridge.py            # 0.0.0.0:8099 → POST /v1/chat/completions, GET /health
    POST /admin/fail {"models": ["anthropic/claude-sonnet-5"]}   # failover drill: make a model id return 503

Protocol (ported from the production platform's CLI seam): messages are flattened into a system prompt and a
conversation transcript; when tools are present the model is asked to answer with ONLY a JSON object
{"content": "...", "tool_calls": [{"name": ..., "arguments": {...}}]}; the reply is parsed leniently and returned
as OpenAI `tool_calls` (finish_reason "tool_calls") or plain content (finish_reason "stop").
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


class _JsonLine(logging.Formatter):
    """One JSON object per line; `json.dumps` escapes quotes inside messages."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {"ts": self.formatTime(record), "level": record.levelname, "msg": record.getMessage()},
            ensure_ascii=False,
        )


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonLine())
logging.basicConfig(level="INFO", handlers=[_handler])
log = logging.getLogger("cli-bridge")

PORT = int(os.environ.get("BRIDGE_PORT", "8099"))
CLI_TIMEOUT_S = int(os.environ.get("BRIDGE_CLI_TIMEOUT_S", "240"))
# Gateway-style model ids (config/models.yaml) → Claude CLI model aliases. The judge tier maps to a different
# MODEL (opus) but not a different family — recorded as a deviation in docs/evals.md.
MODEL_MAP: dict[str, str] = json.loads(
    os.environ.get(
        "BRIDGE_MODEL_MAP",
        json.dumps(
            {
                "anthropic/claude-haiku-4-5": "haiku",
                "anthropic/claude-sonnet-5": "sonnet",
                "anthropic/claude-sonnet-4-6": "sonnet",
                "anthropic/claude-opus-5": "opus",
                "openai/gpt-5.4": "opus",
                "openai/gpt-5.4-mini": "haiku",
                "google/gemini-2.5-pro": "opus",
            }
        ),
    )
)
FAIL_MODELS: set[str] = set(filter(None, os.environ.get("BRIDGE_FAIL_MODELS", "").split(",")))
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")  # the app sends its LangSmith key as bearer; require it

app = FastAPI(title="claude-cli-bridge")


# --------------------------------------------------------------------------- CLI
def resolve_cli() -> str:
    """Return the path of the Claude CLI executable.

    `CLAUDE_CLI_PATH` wins; otherwise `claude.exe` on PATH, then `~/.local/bin/claude.exe`, then `claude` on
    PATH.

    Raises:
        RuntimeError: When no candidate is an existing file.
    """
    explicit = os.environ.get("CLAUDE_CLI_PATH")
    if explicit:
        return explicit
    for cand in (
        shutil.which("claude.exe"),
        str(Path.home() / ".local" / "bin" / "claude.exe"),
        shutil.which("claude"),
    ):
        if cand and Path(cand).is_file():
            return cand
    raise RuntimeError("claude CLI not found; set CLAUDE_CLI_PATH")


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text")
                parts.append(t if isinstance(t, str) else json.dumps(item))
        return "\n".join(p for p in parts if p)
    return str(content)


def flatten(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Flatten chat messages into a system prompt and a conversation transcript.

    Prior tool calls are rendered descriptively (never protocol-shaped) so the model does not imitate the
    transcript instead of emitting the JSON contract.

    Args:
        messages: OpenAI-style chat messages.

    Returns:
        `(system, transcript)`: the system/developer contents joined, and the remaining turns joined.
    """
    system: list[str] = []
    convo: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = _text(m.get("content"))
        if role in ("system", "developer"):
            system.append(content)
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            if calls:
                desc = "; ".join(
                    f"{c.get('function', {}).get('name', '?')}({str(c.get('function', {}).get('arguments', '{}'))[:300]})"
                    for c in calls
                )
                convo.append(f"(assistant ran tools earlier: {desc})")
            if content:
                convo.append(f"Assistant: {content}")
        elif role == "tool":
            convo.append(f"[tool result for call {m.get('tool_call_id', '')}]:\n{content[:12000]}")
        else:
            convo.append(f"User: {content}")
    return "\n\n".join(system), "\n\n".join(convo)


def tool_contract(tools: list[dict[str, Any]], tool_choice: Any) -> str:
    """Build the system-prompt section that lists the tools and states the JSON reply contract.

    Args:
        tools: OpenAI-style tool specs; name, description and parameters are copied into the prompt.
        tool_choice: `auto`, `none`, `required`, a tool name, or a `{"function": {"name": ...}}` object; a
            named tool is forced and `required` demands at least one call.
    """
    specs = [
        {
            "name": t.get("function", {}).get("name"),
            "description": t.get("function", {}).get("description", ""),
            "parameters": t.get("function", {}).get("parameters", {}),
        }
        for t in tools
    ]
    forced = None
    if isinstance(tool_choice, dict):
        forced = (tool_choice.get("function") or {}).get("name")
    elif isinstance(tool_choice, str) and tool_choice not in ("auto", "none", "required"):
        forced = tool_choice
    rule = (
        f'You MUST call the tool "{forced}" exactly once with complete arguments; put nothing else in tool_calls.'
        if forced
        else (
            "Call one or more tools when you need them; when you can give the final answer, return tool_calls as []."
            if tool_choice != "required"
            else "You MUST call at least one tool."
        )
    )
    return (
        "\n\n# Tool protocol\n"
        "The tools below are provided by the CALLING APPLICATION, not by this CLI session. They are not Claude Code "
        "tools, need no MCP registration, and are invoked ONLY by returning the JSON object described here; the "
        "application executes the call and sends the result back as the next message. Never say a tool is "
        "unavailable or unregistered: to use one, return the JSON. AVAILABLE TOOLS (JSON schema):\n"
        + json.dumps(specs, indent=1)
        + "\n\nRespond with ONLY a single JSON object, no prose before or after, no code fences:\n"
        '{"content": "<assistant text, may be empty>", "tool_calls": [{"name": "<tool name>", "arguments": {<args object>}}]}\n'
        + rule
        + " Arguments must be a JSON object matching the tool's parameters schema."
    )


def _balanced(s: str, start: int) -> str | None:
    depth = 0
    in_str = False
    esc = False
    for k in range(start, len(s)):
        ch = s[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : k + 1]
    return None


def _forced_tool(tool_choice: Any) -> str | None:
    if isinstance(tool_choice, dict):
        return (tool_choice.get("function") or {}).get("name")
    if isinstance(tool_choice, str) and tool_choice not in ("auto", "none", "required"):
        return tool_choice
    return None


def parse_tool_reply(text: str, forced: str | None = None) -> dict[str, Any]:
    """Parse the model's reply into `{"content": str, "tool_calls": [{"name", "arguments": dict}]}`.

    Strict parse first, then lenient salvage. With a forced tool (tool_choice), a bare JSON object is taken as
    that tool's arguments: smaller CLI models sometimes answer with the arguments alone (validation run B-F1 —
    the classifier then fell back to triage). In the lenient path an arguments object that exists but cannot
    be parsed yields no tool call at all (regression run R4-1).

    Args:
        text: The raw CLI reply text.
        forced: Name of the forced tool, or None.
    """
    blob = text.strip()
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?\s*|\s*```$", "", blob, flags=re.S).strip()
    start = blob.find("{")
    candidate = _balanced(blob, start) if start >= 0 else None
    if candidate:
        try:
            parsed = json.loads(candidate, strict=False)  # the CLI prints real newlines inside strings
            if forced and isinstance(parsed, dict) and not ("tool_calls" in parsed or "content" in parsed):
                return {"content": "", "tool_calls": [{"name": forced, "arguments": parsed}]}
            if isinstance(parsed, dict) and ("tool_calls" in parsed or "content" in parsed):
                calls = []
                for tc in parsed.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    name = tc.get("name") or (tc.get("function") or {}).get("name")
                    args = tc.get("arguments", (tc.get("function") or {}).get("arguments", {}))
                    if isinstance(args, str):
                        try:
                            args = json.loads(args, strict=False)
                        except json.JSONDecodeError:
                            args = {"_raw": args}
                    if name:
                        calls.append({"name": name, "arguments": args if isinstance(args, dict) else {}})
                return {"content": str(parsed.get("content") or ""), "tool_calls": calls}
        except json.JSONDecodeError:
            pass
    # lenient: find "name": "..." followed by an arguments object
    calls = []
    for m in re.finditer(r'"name"\s*:\s*"([\w.\-]+)"', blob):
        args: dict[str, Any] = {}
        am = re.compile(r'"arguments"\s*:\s*"?\s*\{').search(blob, m.end())
        if am:
            obj = _balanced(blob, am.end() - 1)
            if obj:
                for cand in (obj, obj.replace('\\"', '"')):
                    try:
                        parsed_args = json.loads(cand, strict=False)
                        if isinstance(parsed_args, dict):
                            args = parsed_args
                            break
                    except json.JSONDecodeError:
                        continue
            if not args:
                # the arguments object exists but cannot be parsed (truncated or malformed): an empty tool call would
                # look like a valid structured answer downstream (regression run R4-1) — surface it as no tool call
                log.warning(
                    "tool reply has an unparseable arguments object for %s; returning no tool call",
                    m.group(1),
                )
                return {"content": text, "tool_calls": []}
        calls.append({"name": m.group(1), "arguments": args})
    if calls:
        return {"content": "", "tool_calls": calls}
    return {"content": text, "tool_calls": []}


async def run_cli(system: str, convo: str, cli_model: str) -> tuple[str, dict[str, int], str]:
    """Run one non-interactive `claude -p` call and return `(result_text, usage, stop_reason)`.

    All CLI tools are disallowed and the cwd is the temp directory. The system prompt goes on the command line
    unless it is 20,000 characters or longer, or the CLI is a .cmd/.bat shim and the prompt contains a
    newline; then it is prepended to stdin inside `<system>` tags. Usage counts cache reads and cache
    creation as prompt tokens.

    Args:
        system: The flattened system prompt.
        convo: The transcript sent on stdin (`"(no input)"` when empty).
        cli_model: Claude CLI model alias.

    Raises:
        RuntimeError: On timeout (the process is killed), non-JSON output, or an `is_error` envelope.
    """
    exe = resolve_cli()
    args = [exe, "-p", "--output-format", "json", "--model", cli_model, "--disallowedTools", "*"]
    stdin_text = convo or "(no input)"
    shim = exe.lower().endswith((".cmd", ".bat"))
    if system and len(system) < 20_000 and not (shim and "\n" in system):
        args += ["--system-prompt", system]
    elif system:
        stdin_text = f"<system>\n{system}\n</system>\n\n{stdin_text}"
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=tempfile.gettempdir(),  # neutral cwd: no repo CLAUDE.md bleed
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin_text.encode("utf-8")), timeout=CLI_TIMEOUT_S)
    except TimeoutError as e:
        proc.kill()
        raise RuntimeError("claude CLI timed out") from e
    raw = out.decode("utf-8", "replace").strip()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"claude CLI returned non-JSON: {raw[:200]} / {err.decode('utf-8', 'replace')[:200]}"
        ) from e
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(envelope.get('result'))[:300]}")
    u = envelope.get("usage") or {}
    usage = {
        "prompt_tokens": int(u.get("input_tokens", 0))
        + int(u.get("cache_read_input_tokens", 0))
        + int(u.get("cache_creation_input_tokens", 0)),
        "completion_tokens": int(u.get("output_tokens", 0)),
    }
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return str(envelope.get("result", "")), usage, str(envelope.get("stop_reason", "end_turn"))


# --------------------------------------------------------------------------- HTTP
def _authorized(req: Request) -> bool:
    header = req.headers.get("authorization", "")
    presented = header[7:] if header.lower().startswith("bearer ") else req.headers.get("x-api-key", "")
    return bool(BRIDGE_TOKEN) and hmac.compare_digest(presented.encode(), BRIDGE_TOKEN.encode())


@app.get("/health")
async def health() -> dict[str, Any]:
    """Return the bridge status, the known model ids and the models currently failing for the drill."""
    return {
        "ok": True,
        "service": "claude-cli-bridge",
        "models": sorted(MODEL_MAP),
        "failing": sorted(FAIL_MODELS),
    }


@app.post("/admin/fail")
async def admin_fail(req: Request) -> Any:
    """Replace the failover-drill set with the `models` list in the body; requires the bridge token.

    Args:
        req: The incoming request.

    Returns:
        `{"failing": [...]}`, or a 401 JSON response when unauthorised.
    """
    if not _authorized(req):
        return JSONResponse({"error": {"message": "unauthorized"}}, status_code=401)
    body = await req.json()
    FAIL_MODELS.clear()
    FAIL_MODELS.update(body.get("models") or [])
    log.info("failover drill: failing models=%s", sorted(FAIL_MODELS))
    return {"failing": sorted(FAIL_MODELS)}


@app.post("/v1/chat/completions")
async def chat_completions(req: Request) -> Any:
    """Serve an OpenAI-style chat completion by running the Claude CLI once.

    Returns 401 without the bridge token, 503 for a model in the drill set, 404 for an unknown model id and
    502 when the CLI call fails. With tools, the contract is appended to the system prompt and the reply is
    parsed into `tool_calls` (finish_reason "tool_calls") or plain content ("stop"). `stream: true` returns
    the same message as a minimal SSE stream.

    Args:
        req: The incoming request carrying an OpenAI-style body.
    """
    if not _authorized(req):
        return JSONResponse(
            {"error": {"message": "unauthorized: bearer token required", "type": "authentication_error"}},
            status_code=401,
        )
    body = await req.json()
    model = str(body.get("model") or "")
    if model in FAIL_MODELS:
        return JSONResponse(
            {"error": {"message": f"bridge: {model} is failing (drill)", "type": "server_error"}},
            status_code=503,
        )
    cli_model = MODEL_MAP.get(model)
    if cli_model is None:
        return JSONResponse(
            {
                "error": {
                    "message": f"unknown model id {model!r}; known: {sorted(MODEL_MAP)}",
                    "type": "invalid_request_error",
                }
            },
            status_code=404,
        )
    system, convo = flatten(body.get("messages") or [])
    tools = body.get("tools") or []
    if tools:
        system += tool_contract(tools, body.get("tool_choice"))
    t0 = time.time()
    try:
        text, usage, _stop = await run_cli(system, convo, cli_model)
    except Exception as e:
        log.warning("cli failure model=%s err=%s", model, e)
        return JSONResponse({"error": {"message": str(e)[:300], "type": "server_error"}}, status_code=502)
    if tools:
        log.info("raw reply model=%s: %s", model, text[:700].replace(chr(10), " "))
    message: dict[str, Any] = {"role": "assistant", "content": text}
    finish = "stop"
    if tools:
        parsed = parse_tool_reply(text, _forced_tool(body.get("tool_choice")))
        message["content"] = parsed["content"] or None
        if parsed["tool_calls"]:
            message["tool_calls"] = [
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])},
                }
                for c in parsed["tool_calls"]
            ]
            finish = "tool_calls"
    resp = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage,
        "system_fingerprint": f"claude-cli:{cli_model}",
    }
    log.info(
        "completion model=%s cli=%s tools=%d finish=%s ms=%d tokens=%s",
        model,
        cli_model,
        len(tools),
        finish,
        int((time.time() - t0) * 1000),
        usage,
    )
    if body.get("stream"):
        return StreamingResponse(_sse(resp), media_type="text/event-stream")
    return JSONResponse(resp)


async def _sse(resp: dict[str, Any]):
    """Minimal streaming: one delta chunk carrying the whole message, then usage, then [DONE]."""
    msg = resp["choices"][0]["message"]
    delta: dict[str, Any] = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **tc} for i, tc in enumerate(msg["tool_calls"])]
    chunk = {
        **resp,
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        "usage": None,
    }
    yield f"data: {json.dumps(chunk)}\n\n"
    final = {
        **resp,
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": resp["choices"][0]["finish_reason"]}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


if __name__ == "__main__":
    import uvicorn

    if not BRIDGE_TOKEN:
        raise SystemExit(
            "BRIDGE_TOKEN is required (set it to the LANGSMITH_API_KEY the app presents as bearer)"
        )
    log.info("claude-cli-bridge starting port=%s cli=%s", PORT, resolve_cli())
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")  # noqa: S104 — dev host only
