# Add or modify a subagent

> Change the analyst, or add a second specialist. A subagent is a config artifact, not code: an entry in
> `config/agents.yaml` with `kind: subagent`, a reason the doctrine test accepts, a tool grant, a scope the parent
> routes on, and a Hub prompt. The factory builds it from that entry. For an engineer extending the crew; the blocks
> are templates to adapt.

## The artifact

```yaml
# config/agents.yaml
  analyst:
    identity: { name: analyst, version: 1 }
    kind: subagent
    reason: context_isolation_and_tool_scope      # one of: context_isolation_and_tool_scope, tool_scope_isolation, parallelism, context_isolation
    prompt: analyst                               # prompts/analyst.yaml → pinned Hub commit in config/prompts.yaml
    model_tier: worker
    scope: >-                                     # the routing contract the parent sees as the task description
      Book-of-business analyst. Input: a question about one household, policy, renewal or agreement, or arithmetic
      over such figures. Output: a brief of at most 12 lines, every figure cited as view.column or [doc:<id>], plus
      the ids retrieved. Returns no raw rows and no code.
    tools: [sql_query, documents_search, run_code]
    guardrails: { max_tool_calls: 8 }
```

The parent lists it: `drafter.subagents: [analyst]`. `producery/agents/factory.py` turns the entry into the
`subagents=[…]` spec of `create_deep_agent`; the drafter's only tool is `task`.

## The reason test

A subagent exists for context isolation, tool scope, both, or parallelism: the `reason` field must be one of the
four values the comment above lists, and the doctrine test enforces it together with the grants.

```bash
python -m pytest -q tests/test_config_doctrine.py -k "subagents or least_privilege"
```

If you add `kind: subagent` with another reason, or grant `run_code` to a second agent, this fails before anything
is built. The same file also pins the agent set (`{classifier, extractor, drafter, analyst, judge}`), the drafter's
subagent list (`("analyst",)`) and the analyst's exact grants, so a change to any of them is a change to the test in
the same commit.

## The bounded-summary contract

What crosses back to the parent is the subagent's final message only; its transcript is discarded by the harness
(deepagents `SubAgentMiddleware`). The contract is in the prompt's *Output* section and checked three ways:

- `prompts/analyst.yaml` — "at most 12 lines, every figure cited";
- `producery/agents/middleware.py` `RetrievalFirstMiddleware` — the first move must be a tool call, so the brief
  cannot be invented;
- `producery/guards/citations.py` — the drafter keeps only citations the tools returned this turn.

See it in a trace: the `task` tool run's output is the whole brief; the `analyst` run below it holds the tool calls.

## Modify one

1. Change the scope or tools in `config/agents.yaml`; change the prompt in `prompts/<name>.yaml`.
2. `python -m pytest -q tests/test_config_doctrine.py` — grants and reasons.
3. `python scripts/prompts_push.py` — new Hub commit, pin bumped ([Change a prompt](change-a-prompt.md)).
4. `python -m producery.evals.run crew_e2e --rows crew-009,crew-010` — the retrieval and run_code rows exercise the
   analyst end to end.
5. `docker compose build agent && docker compose up -d agent`.

## Add a second subagent

Copy the `analyst` block under a new name, give it its own prompt file and pin, list it under the parent's
`subagents`, update the three assertions in `tests/test_config_doctrine.py` named above (agent set, subagent list,
grants), and write the eval row whose trajectory assert names it (`must_call: ["task", …]`; the subagent is reached
through `task`, so no new name is needed in `KNOWN_TOOL_NAMES` unless it gets a new tool). Before you do: if the
new specialist needs no tools the analyst lacks and no isolation the analyst lacks, it is a prompt section, not a
subagent; the smallest shape that works wins (`../design.md` §3).

## What to read next

- [Add a new MCP tool](add-a-tool.md) — the capability the subagent will need.
- [Run and extend the evals](evals.md) — the trajectory assert.
