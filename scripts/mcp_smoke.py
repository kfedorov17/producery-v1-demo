"""Live MCP tool checks against the running stack: every tool with valid and invalid args (CP2 / CP4 evidence).

python scripts/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import json

from producery.settings import get_settings

MCP_DATA = get_settings().eval_mcp_data_url
MCP_EXEC = get_settings().eval_mcp_exec_url

CASES = [
    (
        "data",
        "sql_query",
        {
            "query": "select household_name, policy_number, premium_change_pct from v_renewals order by x_date limit 3"
        },
    ),
    ("data", "sql_query", {"query": "select * from households"}),
    ("data", "sql_query", {"query": "drop table policies"}),
    (
        "data",
        "documents_search",
        {"query": "non-compete after termination", "doc_types": ["contract"], "k": 2},
    ),
    ("data", "documents_search", {"query": "ab"}),
    (
        "exec",
        "run_code",
        {
            "code": "import json\nprint(json.dumps({'sum': sum(inputs['xs'])}))",
            "inputs": {"xs": [1, 2, 3]},
            "timeout_s": 10,
        },
    ),
    ("exec", "run_code", {"code": "while True: pass", "timeout_s": 2}),
    (
        "exec",
        "run_code",
        {
            "code": "import urllib.request\ntry:\n    urllib.request.urlopen('https://example.com', timeout=3); print('EGRESS-ALLOWED')\nexcept Exception as e:\n    print('blocked:', type(e).__name__)",
            "timeout_s": 10,
        },
    ),
    (
        "exec",
        "run_code",
        {
            "code": "import os\nopen('/work/marker.txt','w').write('x') if os.path.isdir('/work') else None\nprint('cwd files:', os.listdir('.'))\nprint('work files:', os.listdir('/work') if os.path.isdir('/work') else 'n/a')",
            "timeout_s": 10,
        },
    ),
    ("exec", "run_code", {"code": "", "timeout_s": 5}),
]


async def main() -> int:
    """Invoke every case in `CASES` through the MCP adapters client and print each result as ok or ERROR.

    Tool errors are handled by the client (`handle_tool_errors=True`) and a non-JSON reply is printed as a
    `tool_error` object. Returns 0; nothing is asserted.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "data": {"transport": "streamable_http", "url": MCP_DATA},
            "exec": {"transport": "streamable_http", "url": MCP_EXEC},
        },
        handle_tool_errors=True,
    )
    tools = {t.name: t for t in await client.get_tools()}
    print("tools:", {n: (t.description or "")[:70] for n, t in tools.items()})
    for server, name, args in CASES:
        raw = await tools[name].ainvoke(args)
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            raw = raw[0].get("text", raw)
        try:
            res = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            res = {"error": {"code": "tool_error", "message": str(raw)[:300]}}
        kind = "ERROR " if isinstance(res, dict) and "error" in res else "ok    "
        print(f"[{server}] {name} {json.dumps(args)[:70]}\n   {kind}{json.dumps(res, default=str)[:400]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
