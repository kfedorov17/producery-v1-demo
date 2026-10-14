# Add a new MCP tool

> Add a capability the analyst can call. A tool is a typed function on one of the two MCP servers, a registry
> entry, a grant in `config/agents.yaml`, and an eval row. Nothing else: no model inside the tool, no ad-hoc import
> in the agent. The blocks are a worked example (`policy_lookup`); the last section lists the five places that name tools and must agree.

## 1. Define it on the right server

Data tools live in `producery/mcp_data/server.py` (they run as `producery_readonly`); anything that executes code
goes through `producery/mcp_exec/server.py`. Copy the shape of `documents_search`: a typed signature, a
description that teaches the model when to use it, a structured result, and a structured error.

```python
# producery/mcp_data/server.py — inside build_server(), next to documents_search
@mcp.tool(
    name="policy_lookup",
    description="Look up ONE policy by its number (e.g. HO-1001). Returns the row from v_policies plus a citation string.",
)
async def policy_lookup(policy_number: str) -> dict:
    rows, _ = await db.fetch_as_role(
        "select * from v_policies where policy_number = %s", settings.database_readonly_role, row_cap=1, params=(policy_number,)
    )
    if not rows:
        return {"error": {"code": "not_found", "message": f"no policy {policy_number}", "hint": "Search v_policies by household_name first"}}
    return {"row": rows[0], "citation": f"v_policies · {policy_number}"}
```

## 2. Register the name

The registry is an explicit dict; an unknown name fails at startup, a missing tool on the server fails at startup.

```python
# producery/tools/registry.py
KNOWN_TOOLS: dict[str, str] = {"sql_query": "data", "documents_search": "data", "run_code": "exec", "policy_lookup": "data"}
```

## 3. Grant it, and move the doctrine test with it

Only the analyst gets data tools; the drafter's `tools: []` is a doctrine test. The same test
(`tests/test_config_doctrine.py`, `test_least_privilege_tool_grants`) asserts the analyst's grants are exactly
`{sql_query, documents_search, run_code}`, so the unit stage goes red until the grant and the test change in the
same commit:

```yaml
# config/agents.yaml
  analyst:
    tools: [sql_query, documents_search, run_code, policy_lookup]
```

```python
# tests/test_config_doctrine.py
    assert set(cfg.agents["analyst"].tools) == {"sql_query", "documents_search", "run_code", "policy_lookup"}
```

Prove the fail-fast seam without starting anything: granting a name the registry does not have raises.

```bash
python -c "
from producery.tools.registry import ToolRegistry
try:
    ToolRegistry.empty().resolve(['policy_lookup'], agent='analyst')
except RuntimeError as e:
    print('startup would fail:', e)"
```

```text
startup would fail: agent 'analyst' grants unknown tools ['policy_lookup']; registry has []
```

## 4. Add the eval row that must accompany it

Every tool path has a row in `evals/datasets/tools.jsonl` that calls it directly, with a `tool_result` check
and a latency bound; a trajectory assert in `crew_e2e.jsonl` proves the agent actually reaches for it. A trajectory
assert may only name tools listed in `KNOWN_TOOL_NAMES` (`producery/evals/evaluators.py`); an unknown name raises
at load time and the gate counts it as an evaluator error (G8), so add the new name there first.

```json
{"inputs": {"tool": "policy_lookup", "args": {"policy_number": "HO-1001"}},
 "outputs": {"expect": "ok", "contains": ["Sam Patel", "v_policies"]},
 "metadata": {"row_id": "tool-policy-lookup", "checks": [{"name": "tool_result"}, {"name": "latency_under", "args": {"seconds": 10}}], "tags": ["must-pass", "tool:policy_lookup"]}}
```

## 5. Rebuild, run the tool suite, check the prompt

```bash
docker compose build mcp-data agent && docker compose up -d mcp-data agent
python -m pytest -q tests/test_config_doctrine.py          # the grant and the test agree
python scripts/mcp_smoke.py                                 # the hard-coded CASES list: add a valid and an invalid call for the new tool
python -m producery.evals.run tools --local && python -m producery.evals.gate
```

The analyst's prompt (`prompts/analyst.yaml`, section *How*) names the tools it may call; add one line and follow
[Change a prompt](change-a-prompt.md) so the pin moves with the code.

## The five places that name tools

| Where | What it holds | What breaks if you forget it |
|---|---|---|
| `producery/mcp_data/server.py` or `producery/mcp_exec/server.py` | the function | the registry refuses to start: `MCP servers did not expose expected tools` |
| `producery/tools/registry.py` `KNOWN_TOOLS` | name → server | an ungranted name is "unknown" at startup |
| `config/agents.yaml` + `tests/test_config_doctrine.py` | the grant and the test that pins it | the unit stage |
| `producery/evals/evaluators.py` `KNOWN_TOOL_NAMES` | names a trajectory assert may use | a `ValueError` at load, counted by G8 |
| `scripts/mcp_smoke.py` `CASES`, `prompts/analyst.yaml` | the smoke calls; what the model is told it may call | the tool is never exercised, or never used |

## What to read next

- [Execute code safely](sandbox.md) — if the new capability needs computation, it belongs behind `run_code`.
- [Run and extend the evals](evals.md) — the trajectory assert that proves the tool is used.
