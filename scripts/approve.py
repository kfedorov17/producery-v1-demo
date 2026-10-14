"""Human-in-the-loop CLI against the running agent server.

python scripts/approve.py list                       # paused threads awaiting a decision
python scripts/approve.py show crew:<email-id>
python scripts/approve.py approve crew:<email-id> [--by sam]
python scripts/approve.py edit crew:<email-id> --body "..." [--subject "..."]
python scripts/approve.py reject crew:<email-id> --reason "..."
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from producery.settings import get_settings

AGENT = get_settings().eval_agent_url
TOKEN = get_settings().agent_api_token
DB = os.environ.get("DATABASE_URL_LOCAL", "postgresql://producery:producery@localhost:5444/producery")


def _paused() -> list[dict]:
    import psycopg

    with psycopg.connect(DB) as conn:
        rows = conn.execute(
            "select id, thread_id, subject, status from inbox where status='paused' order by updated_at"
        ).fetchall()
    return [{"email_id": r[0], "thread_id": r[1], "subject": r[2], "status": r[3]} for r in rows]


def main() -> int:
    """Run one human-in-the-loop command against the agent server and return the exit code.

    `list` reads paused threads straight from the database; `show` fetches the thread; `approve`, `edit` and
    `reject` POST a decision to `/threads/<id>/resume`. For `edit`, the interrupt payload is fetched first and
    `--body` / `--subject` are merged into it. Exit codes: 0 on success, 1 on a non-200 resume, 2 when the
    thread id is missing.
    """
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["list", "show", "approve", "edit", "reject"])
    p.add_argument("thread_id", nargs="?")
    p.add_argument("--by", default="reviewer")
    p.add_argument("--reason")
    p.add_argument("--body")
    p.add_argument("--subject")
    a = p.parse_args()
    client = httpx.Client(base_url=AGENT, timeout=300, headers={"authorization": f"Bearer {TOKEN}"})
    if a.cmd == "list":
        for row in _paused():
            print(json.dumps(row))
        return 0
    if not a.thread_id:
        print("thread_id required", file=sys.stderr)
        return 2
    if a.cmd == "show":
        r = client.get(f"/threads/{a.thread_id}")
        print(json.dumps(r.json(), indent=2, default=str))
        return 0
    decision: dict = {"action": a.cmd, "by": a.by, "reason": a.reason}
    if a.cmd == "edit":
        state = client.get(f"/threads/{a.thread_id}").json()
        payload = dict((state.get("interrupt") or {}).get("payload") or {})
        if a.body:
            payload["body"] = a.body
        if a.subject:
            payload["subject"] = a.subject
        decision["payload"] = payload
    r = client.post(f"/threads/{a.thread_id}/resume", json=decision)
    print(r.status_code, json.dumps(r.json(), indent=2, default=str))
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
