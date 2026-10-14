"""Demo driver against the running stack.

python scripts/demo.py inbox                     # fixture inbox with statuses
python scripts/demo.py release <email-id>        # hand a held fixture to the worker (poll_inbox → run_crew)
python scripts/demo.py run <email-id>            # run synchronously through the agent API (bypasses the worker)
python scripts/demo.py docs                      # documents + ingestion status
python scripts/demo.py search "non-compete"      # documents_search via the MCP server
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx

from producery.settings import get_settings

AGENT = get_settings().eval_agent_url
TOKEN = get_settings().agent_api_token
MCP_DATA = get_settings().eval_mcp_data_url
DB = os.environ.get("DATABASE_URL_LOCAL", "postgresql://producery:producery@localhost:5444/producery")


def _db():
    import psycopg

    return psycopg.connect(DB)


async def _search(query: str) -> None:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({"data": {"transport": "streamable_http", "url": MCP_DATA}})
    tools = {t.name: t for t in await client.get_tools()}
    raw = await tools["documents_search"].ainvoke({"query": query, "k": 3})
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):  # MCP content blocks (adapters ≥ 0.3)
        raw = raw[0].get("text", raw)
    print(json.dumps(json.loads(raw) if isinstance(raw, str) else raw, indent=2)[:3000])


def main() -> int:
    """Dispatch the sub-command named in `sys.argv[1]` (default `inbox`) and return the exit code.

    Unknown commands print the module usage and return 2; `run` returns 1 when the agent API does not answer
    200.
    """
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inbox"
    if cmd == "inbox":
        with _db() as conn:
            for r in conn.execute(
                "select id, status, thread_id, subject from inbox order by received_at"
            ).fetchall():
                print(f"{r[0]:22s} {r[1]:8s} {r[2] or '-':28s} {r[3]}")
        return 0
    if cmd == "release":
        with _db() as conn:
            conn.execute("update inbox set status='new', updated_at=now() where id=%s", (sys.argv[2],))
        print("released", sys.argv[2])
        return 0
    if cmd == "run":
        r = httpx.post(
            f"{AGENT}/runs",
            json={"email_id": sys.argv[2]},
            timeout=600,
            headers={"authorization": f"Bearer {TOKEN}"},
        )
        print(r.status_code, json.dumps(r.json(), indent=2, default=str)[:5000])
        return 0 if r.status_code == 200 else 1
    if cmd == "docs":
        with _db() as conn:
            for r in conn.execute(
                "select id, doc_type, status, chunk_count from documents order by id"
            ).fetchall():
                print(f"{r[0]:26s} {r[1]:9s} {r[2]:10s} chunks={r[3]}")
        return 0
    if cmd == "search":
        asyncio.run(_search(sys.argv[2]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
