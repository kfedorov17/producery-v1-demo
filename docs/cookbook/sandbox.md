# Execute code safely

> Call the sandbox directly and see each limit hold. `run_code` is the only way code runs: an MCP tool on
> `mcp-exec` that forwards to the `executor` service, a fresh `python -I` process per request, unprivileged, with
> rlimits, no network, in a directory destroyed afterwards. For an engineer who needs a computed figure, or wants
> to check the boundary.

## Call it directly

The smoke script calls every tool with valid and invalid arguments; the `run_code` cases are the ones you want.

```bash
python scripts/mcp_smoke.py
```

```text
[exec] run_code {"code": "import json\nprint(json.dumps({'sum': sum(inputs['xs'])}))", ...
   ok    {"stdout": "{\"sum\": 6}\n", "stderr": "", "exit_status": 0, "timed_out": false, "duration_ms": ..., "executor": "sealed-process"}
[exec] run_code {"code": "while True: pass", "timeout_s": 2}
   ok    {"stdout": "", "exit_status": -9, "timed_out": true, ...}
[exec] run_code {"code": "import urllib.request ... urlopen('https://example.com')"}
   ok    {"stdout": "blocked: URLError\n", ...}
```

A timeout is a structured result, not an error; egress is refused by the network, not by the code.

## From Python

```python
# cb_sandbox.py — run with the repo's venv; uses the same adapter the agent uses
import asyncio, json
from langchain_mcp_adapters.client import MultiServerMCPClient

async def main():
    client = MultiServerMCPClient({"exec": {"transport": "streamable_http", "url": "http://localhost:8092/mcp"}})
    tools = {t.name: t for t in await client.get_tools()}
    raw = await tools["run_code"].ainvoke({
        "code": "print(open('/proc/self/status').read().split('CapEff:')[1].split()[0]); import os; print(os.getuid())",
        "timeout_s": 10,
    })
    if isinstance(raw, list):
        raw = raw[0]["text"]
    print(json.loads(raw)["stdout"])

asyncio.run(main())
```

```text
0000000000000000
10002
```

No capabilities, an unprivileged uid. `inputs` (a JSON object) is available to the snippet as `inputs`.

## What is blocked, and how

The boundary (network, secrets, privilege, rlimits, wall clock, per-request directory) is described once in
`../design.md` §6; the mechanisms live in `docker-compose.yml` (`exec_net`, `cap_drop`, `read_only`, tmpfs, the
two-variable environment) and `producery/executor/server.py` (uid drop, rlimits, the process group kill, the
run directory).

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T executor ls -A /work      # empty between runs (the prefix stops Git Bash rewriting /work)
```

The daemon reads two more variables with defaults, `EXEC_MEM_BYTES` (256 MB address-space limit per snippet) and
`EXEC_SANDBOX_USER` (`sandbox`, the unprivileged runner account); compose sets neither.

## Who may call it

Only the analyst (`config/agents.yaml` → `tools: [sql_query, documents_search, run_code]`); the doctrine test
pins that. The analyst cites computed figures as "computed via run_code" and the eval row `crew-010` asserts the
trajectory reached the tool.

## What to read next

- [Add a new MCP tool](add-a-tool.md) — if the computation deserves a named tool instead of a snippet.
- `../deployment.md` §5 — the executor maps to LangSmith Sandboxes with the same `run_code` contract.
