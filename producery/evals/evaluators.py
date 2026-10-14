"""Register every evaluator in one allowlist shared by experiments, the local recorder, the smoke eval and the gate.

Deterministic first: never pay a judge for what a regex can score. Every check is `fn(run, example) ->
{"key", "score", "comment"}`. Rows name their checks in `metadata.checks`; an unknown name fails fast.

Every check runs SDK-local inside `langsmith.evaluate()`. In the self-hosted posture the deterministic checks can be
registered as custom-code evaluators that run server-side in LangSmith's ACE backend; the judge stays SDK-local so its
model family is pinned by config.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from pydantic import ValidationError

from producery.contracts import DraftResponse

_DOC_ID_RE = re.compile(r"doc-[\w\-]+#\d+")


def _out(run: Any) -> dict[str, Any]:
    o = getattr(run, "outputs", None) or {}
    return o if isinstance(o, dict) else {}


def _exp(example: Any) -> dict[str, Any]:
    return getattr(example, "outputs", None) or {}


def _text_of(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False).lower()


# tool names a trajectory assert may reference: the MCP tools plus the deepagents delegation tool
KNOWN_TOOL_NAMES = frozenset({"sql_query", "documents_search", "run_code", "task"})


# ----------------------------------------------------------------------------- deterministic
def classification_correct(run, example):
    """Score 1 when the run's classification equals the expected one; key `classification`.

    Args:
        run: The target run; its outputs are read.
        example: The dataset example; its expected outputs are read.
    """
    want = _exp(example).get("classification")
    got = (_out(run).get("classification") or {}).get("classification")
    return {"key": "classification", "score": int(got == want), "comment": f"want {want}, got {got}"}


def path_match(run, example):
    """Score 1 when the run's node path equals the expected list exactly; key `path`.

    Args:
        run: The target run; its outputs are read.
        example: The dataset example; its expected outputs are read.
    """
    want = _exp(example).get("path", [])
    got = _out(run).get("path", [])
    return {"key": "path", "score": int(got == want), "comment": f"want {want}, got {got}"}


def outcome_equals(run, example):
    """Score 1 when the run's outcome equals the expected one; key `outcome`.

    Args:
        run: The target run; its outputs are read.
        example: The dataset example; its expected outputs are read.
    """
    want = _exp(example).get("outcome")
    got = _out(run).get("outcome")
    return {"key": "outcome", "score": int(got == want), "comment": f"want {want}, got {got}"}


def not_contains(run, example):
    """Score 1 when none of `must_not_contain` appears (case-insensitive) in the draft subject or body.

    Args:
        run: The target run; its `draft` output is read.
        example: The dataset example; its `must_not_contain` list is read.

    Returns:
        A result under key `not_contains` whose comment lists the strings found.
    """
    banned = _exp(example).get("must_not_contain", [])
    draft = _out(run).get("draft") or {}
    text = f"{draft.get('subject', '')}\n{draft.get('body', '')}".lower()
    hits = [b for b in banned if str(b).lower() in text]
    return {"key": "not_contains", "score": int(not hits), "comment": f"contains {hits}"}


def compliance_clean(run, example):
    """Score 1 when the run raised no compliance flags; key `compliance_clean`.

    Args:
        run: The target run; its `compliance_flags` output is read.
        example: Unused.
    """
    flags = _out(run).get("compliance_flags") or []
    return {"key": "compliance_clean", "score": int(not flags), "comment": json.dumps(flags)[:200]}


def injection_ignored(run, example):
    """Score 1 when no `injection_markers` leaked into the draft, proposal, extraction or classification.

    Args:
        run: The target run; the four outputs are serialised and searched case-insensitively.
        example: The dataset example; its `injection_markers` list is read.

    Returns:
        A result under key `injection` whose comment lists the leaked markers.
    """
    markers = _exp(example).get("injection_markers", [])
    text = _text_of({k: _out(run).get(k) for k in ("draft", "proposal", "extraction", "classification")})
    leaked = [m for m in markers if str(m).lower() in text]
    return {"key": "injection", "score": int(not leaked), "comment": f"leaked {leaked}"}


def mentions_required(run, example):
    """Score 1 when every required mention and regex is found in the draft, proposal or extraction.

    Args:
        run: The target run; the three outputs are serialised and searched case-insensitively.
        example: The dataset example; `answer_must_mention` (substrings) and `answer_must_match` (regexes) are
            read.

    Returns:
        A result under key `mentions` whose comment lists what is missing.
    """
    required = _exp(example).get("answer_must_mention", [])
    patterns = _exp(example).get("answer_must_match", [])  # regexes, for figures whose formatting may vary
    text = _text_of({k: _out(run).get(k) for k in ("draft", "proposal", "extraction")})
    missing = [m for m in required if str(m).lower() not in text]
    missing += [f"/{p}/" for p in patterns if not re.search(p, text, re.I)]
    return {"key": "mentions", "score": int(not missing), "comment": f"missing {missing}"}


def field_equals(field: str, expected: Any):
    """Build a check that compares one dotted output field with an expected value; key `field:<field>`.

    A `{"value": ...}` wrapper is unwrapped. Numbers match within max(0.5, 0.1 percent of expected); strings
    match on equality or case-insensitive containment; anything else on equality. A missing field reads as
    None.

    Args:
        field: Dotted path into the run outputs, e.g. `extraction.policy_number`.
        expected: The expected value.

    Returns:
        A `(run, example)` evaluator.
    """

    def _check(run, example):
        del example
        cur: Any = _out(run)
        for part in field.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if isinstance(cur, dict) and "value" in cur:
            cur = cur["value"]
        if isinstance(expected, (int, float)) and isinstance(cur, (int, float)):
            ok = abs(float(cur) - float(expected)) <= max(
                0.5, abs(float(expected)) * 0.001
            )  # tolerance check
        else:
            ok = cur == expected or (
                isinstance(cur, str) and isinstance(expected, str) and expected.lower() in cur.lower()
            )
        return {"key": f"field:{field}", "score": int(bool(ok)), "comment": f"want {expected!r}, got {cur!r}"}

    return _check


def schema_valid(run, example):
    """Score 1 when the run's draft validates as a `DraftResponse`; key `schema_valid`.

    Args:
        run: The target run; its `draft` output is validated. A missing draft scores 0.
        example: Unused.
    """
    del example
    draft = _out(run).get("draft")
    if draft is None:
        return {"key": "schema_valid", "score": 0, "comment": "no draft"}
    try:
        DraftResponse.model_validate(draft)
        return {"key": "schema_valid", "score": 1}
    except ValidationError as e:
        return {"key": "schema_valid", "score": 0, "comment": str(e)[:200]}


def _norm_cite(c: str) -> str:
    """'[doc:doc-x#7]' / 'doc:doc-x#7' → 'doc-x#7'; 'v_renewals.x_date · HO-1001' → 'v_renewals.x_date'."""
    s = str(c).strip().split("·")[0].split(" ")[0].strip("[]")
    return s[4:] if s.startswith("doc:") else s


def cites_approved(run, example):  # cited ⊆ retrieved ∩ approved
    """Score 1 when every citation is an approved source that a tool call also retrieved; key `cites`.

    Retrieved ids come from the tool calls' `output_ids` plus any `v_` view named in a `sql_query` query. A
    run with no citations scores 0.

    Args:
        run: The target run; its `citations` and `tool_calls` outputs are read.
        example: The dataset example; its `approved_sources` list is read.
    """
    approved = {_norm_cite(a) for a in _exp(example).get("approved_sources", [])}
    cited = {_norm_cite(c) for c in (_out(run).get("citations") or []) if str(c).strip()}
    retrieved: set[str] = set()
    for call in _out(run).get("tool_calls") or []:
        retrieved.update(call.get("output_ids") or [])
        args = call.get("args") or {}
        if call.get("name") == "sql_query":
            retrieved.update(re.findall(r"v_\w+", str(args.get("query", ""))))
    if not cited:
        return {"key": "cites", "score": 0, "comment": "no citations"}
    bad_approved = [c for c in cited if not any(c.startswith(a) for a in approved)]
    bad_retrieved = [
        c
        for c in cited
        if not any(c == r or c.startswith(r) or r.startswith(c.split(".")[0]) for r in retrieved)
    ]
    ok = not bad_approved and not bad_retrieved
    return {
        "key": "cites",
        "score": int(ok),
        "comment": f"not approved {bad_approved}; not retrieved {bad_retrieved}",
    }


def trajectory(
    must_call: list[str] | None = None,
    must_not_call: list[str] | None = None,
    args: dict[str, str] | None = None,
    max_steps: int | None = None,
    ordered: bool = False,
    precedes: dict[str, str] | None = None,
):
    """Build a check over the run's recorded tool calls; key `trajectory`.

    `ordered=True` requires `must_call` to appear as a subsequence in call order. `precedes={"run_code":
    "sql_query"}` requires EVERY call of the key to come after the FIRST call of the value — the check that
    catches a computation on guessed inputs before the retrieval (a subsequence check is satisfied by a later,
    correct call).

    Args:
        must_call: Tool names that must each appear at least once.
        must_not_call: Tool names that must not appear.
        args: Tool name to regex; at least one call of that tool must have JSON args matching it (ignored when
            the tool was never called).
        max_steps: Maximum number of tool calls allowed.
        ordered: Whether `must_call` must also appear in that order.
        precedes: Later tool name to earlier tool name, as described above.

    Returns:
        A `(run, example)` evaluator that scores 1 when no problem is found and lists the problems in its
        comment.

    Raises:
        ValueError: If any named tool is not in `KNOWN_TOOL_NAMES`.
    """
    for n in (
        *(must_call or []),
        *(must_not_call or []),
        *(args or {}),
        *(precedes or {}),
        *(precedes or {}).values(),
    ):
        if n not in KNOWN_TOOL_NAMES:
            raise ValueError(f"trajectory check names unknown tool {n!r}; known: {sorted(KNOWN_TOOL_NAMES)}")

    def _check(run, example):
        del example
        calls = _out(run).get("tool_calls") or []
        names = [c.get("name") for c in calls]
        problems = []
        for n in must_call or []:
            if n not in names:
                problems.append(f"missing call {n}")
        if ordered and must_call and not problems:
            pos = 0
            for n in must_call:
                try:
                    pos = names.index(n, pos) + 1
                except ValueError:
                    problems.append(
                        f"out of order: expected {' -> '.join(must_call)}, got {' -> '.join(names)}"
                    )
                    break
        for n in must_not_call or []:
            if n in names:
                problems.append(f"forbidden call {n}")
        for n, pattern in (args or {}).items():
            matching = [c for c in calls if c.get("name") == n]
            if matching and not any(
                re.search(pattern, json.dumps(c.get("args") or {}), re.I) for c in matching
            ):
                problems.append(f"args of {n} never matched /{pattern}/")
        for later, earlier in (precedes or {}).items():
            first = names.index(earlier) if earlier in names else None
            bad = [i for i, n in enumerate(names) if n == later and (first is None or i < first)]
            if bad:
                problems.append(
                    f"{later} at #{bad[0]} precedes first {earlier} (calls: {' -> '.join(names)})"
                )
        if max_steps is not None and len(calls) > max_steps:
            problems.append(f"{len(calls)} tool calls > max_steps {max_steps}")
        return {"key": "trajectory", "score": int(not problems), "comment": "; ".join(problems)[:300]}

    return _check


def tool_result(run, example):
    """Score a direct tool invocation against the row's expectation; key `tool_result`.

    With `expect: error` the result's `error.code` must equal `error_code` and no traceback may leak.
    Otherwise the result must carry no `error`, contain every `contains` string in its JSON and match every
    `equals` entry.

    Args:
        run: The target run; its `result` output is read (a non-dict result counts as empty).
        example: The dataset example; `expect`, `error_code`, `contains` and `equals` are read.
    """
    exp = _exp(example)
    out = _out(run)
    raw_result = out.get("result")
    result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
    if exp.get("expect") == "error":
        code = ((result or {}).get("error") or {}).get("code")
        ok = code == exp.get("error_code") and "Traceback" not in json.dumps(result)
        return {
            "key": "tool_result",
            "score": int(ok),
            "comment": f"want error {exp.get('error_code')}, got {code}",
        }
    text = json.dumps(result, default=str)
    missing = [s for s in exp.get("contains", []) if s not in text]
    ok = (
        "error" not in result
        and not missing
        and all(result.get(k) == v for k, v in (exp.get("equals") or {}).items())
    )
    return {
        "key": "tool_result",
        "score": int(ok),
        "comment": f"missing {missing}; error={result.get('error')}",
    }


def latency_under(seconds: float):
    """Build a check that scores 1 when the run's `latency_s` output is at most `seconds`; key `latency`.

    Args:
        seconds: The latency limit in seconds.

    Returns:
        A `(run, example)` evaluator.
    """

    def _check(run, example):
        del example
        lat = float(_out(run).get("latency_s") or 0.0)
        return {"key": "latency", "score": int(lat <= seconds), "comment": f"{lat:.1f}s (limit {seconds}s)"}

    return _check


# ----------------------------------------------------------------------------- LLM-as-judge (cross-family, anchored)
def judge_rubric():
    """Build the anchored 0-3 judge check from the Hub prompt `judge_draft`; key `judge`.

    The judge runs on the `judge` tier (a different family than the worker tier under test) with the tier's
    declared fallbacks, at temperature 0. The score is the check value so the gate computes mean + zero floor.
    A run without a draft body scores 0 without calling the model.

    Returns:
        A `(run, example)` evaluator that sends the example inputs and the draft to the judge model.
    """

    def _check(run, example):
        from producery.contracts import JudgeVerdict
        from producery.evals.context import eval_context

        ctx = eval_context()
        draft = _out(run).get("draft") or {}
        if not draft.get("body"):
            return {"key": "judge", "score": 0, "comment": "no draft body to judge"}
        prompt = ctx.prompts.get("judge_draft")
        model = ctx.models.for_agent("judge@1", "judge").with_structured_output(
            JudgeVerdict, method="function_calling"
        )
        fbs = [
            m.with_structured_output(JudgeVerdict, method="function_calling")
            for m in ctx.models.fallbacks_for("judge@1", "judge")
        ]
        if fbs:
            model = model.with_fallbacks(fbs)
        inputs = json.dumps(getattr(example, "inputs", {}) or {}, default=str)[:3000]
        verdict = cast(
            JudgeVerdict,
            model.invoke(
                prompt.template.format_messages(
                    input=f"INPUTS:\n{inputs}\n\nANSWER (client draft):\n{json.dumps(draft, default=str)[:3000]}"
                ),
                config={
                    "run_name": "judge",
                    "tags": ["agent:judge"],
                    "metadata": {"agent": "judge@1", "prompt": prompt.ref},
                },
            ),
        )
        return {"key": "judge", "score": int(verdict.score), "comment": verdict.reason[:300]}

    return _check


CHECKS: dict[str, Any] = {
    "classification_correct": lambda: classification_correct,
    "path_match": lambda: path_match,
    "outcome_equals": lambda: outcome_equals,
    "not_contains": lambda: not_contains,
    "compliance_clean": lambda: compliance_clean,
    "injection_ignored": lambda: injection_ignored,
    "mentions_required": lambda: mentions_required,
    "field_equals": lambda **kw: field_equals(**kw),
    "schema_valid": lambda: schema_valid,
    "cites_approved": lambda: cites_approved,
    "trajectory": lambda **kw: trajectory(**kw),
    "tool_result": lambda: tool_result,
    "latency_under": lambda **kw: latency_under(**kw),
    "judge_rubric": lambda: judge_rubric(),
}
DETERMINISTIC = set(CHECKS) - {"judge_rubric"}
TRAJECTORY_KEYS = {"path", "trajectory"}
# measurements, not verdicts: recorded as feedback on every row, read by the gate's G9, never pass/fail
METRIC_KEYS = {"tokens", "cost_usd"}


def usage_metrics(run, example):
    """Tokens and estimated cost of the row's turn(s) — from the agent's usage summary."""
    del example
    out = _out(run)
    if out.get("tokens") is None:
        return {"results": []}  # tool rows have no model calls
    return {
        "results": [
            {"key": "tokens", "score": int(out.get("tokens") or 0), "comment": "total tokens, all models"},
            {"key": "cost_usd", "score": float(out.get("cost_usd") or 0.0), "comment": "list-price estimate"},
        ]
    }


def row_checks_evaluator(run, example):
    """Run every check the example names in `metadata.checks` and return their results together.

    One check raising (a judge timeout, a harness error) must not erase the row's other verdicts: the failed
    check reports `score: None` under its own name (the gate's G8 counts it), the deterministic results stand.

    Args:
        run: The target run.
        example: The dataset example whose `metadata.checks` lists the checks.

    Returns:
        `{"results": [...]}` in the LangSmith multi-result shape.
    """
    checks = (getattr(example, "metadata", None) or {}).get("checks", [])
    results = []
    for c in checks:
        try:
            results.append(CHECKS[c["name"]](**c.get("args", {}))(run, example))
        except Exception as e:  # every evaluator failure is an error verdict, never a crash
            results.append({"key": c["name"], "score": None, "comment": f"evaluator_error: {e!r}"[:300]})
    return {"results": results}
