# Run your first agent turn

> Send one inbound email through the crew, read the proposal it pauses on, approve it, and open the trace.
> For a developer running the stack for the first time.

## Choose the model door

The application holds no provider keys: model calls go to `LANGSMITH_GATEWAY_BASE_URL` with your LangSmith key. If
the LLM Gateway is enabled on your organisation the `.env` defaults are right and nothing below applies. If it is
not (free organisations: the first drafting turn ends `failed` with `403 LLM Gateway not enabled` in the trace),
run the flagged interim door on the host. It turns a logged-in Claude Code CLI into the same OpenAI-compatible
endpoint:

```bash
# .env
MODEL_ACCESS_MODE=langchain
LANGSMITH_GATEWAY_BASE_URL=http://host.docker.internal:8099/v1
MODEL_DOOR_LABEL="claude-cli-bridge (interim, option 3)"
```

```bash
make bridge                                   # host port 8099; keep it running
docker compose up -d agent worker             # if the stack was already up when you changed .env
curl -s localhost:8010/ready                  # "model_door":"langchain-interim"
```

`/ready` names the active door either way (`model_door`), and so does every trace (`model_door_label`). The bridge
reads `BRIDGE_TOKEN` (required) and, with defaults, `BRIDGE_PORT` (8099), `BRIDGE_CLI_TIMEOUT_S` (240),
`BRIDGE_MODEL_MAP` (model id to CLI alias), `BRIDGE_FAIL_MODELS` (the failover drill) and `CLAUDE_CLI_PATH`.

## Send an email

Every thread is `crew:<email id>`; pick an id you have not used (an executed thread answers
`proposal_already_executed` on a re-run — that is correct, not a bug).

```bash
TOK=$(grep -E '^AGENT_API_TOKEN=' .env | cut -d= -f2-)      # the bearer token every agent route needs
curl -s -X POST localhost:8010/runs -H "authorization: Bearer $TOK" -H 'content-type: application/json' \
  -d '{"wait":false,"email":{"id":"cb-first-1","from_email":"sam@example.net","from_name":"Sam Patel","subject":"renewal","body":"What changed on my homeowners renewal this year?"}}'
```

```text
{"thread_id":"crew:cb-first-1","status":"running"}
```

The same email can arrive through the worker instead (`python scripts/demo.py release renewal-question` hands a
held fixture to `poll_inbox`) or through the producer UI (Connections → Email → Capture).

## Wait for the pause

The graph stops at `apply_decision` with a dynamic `interrupt()`; the thread reports `paused`. Wait for `paused`
or for a terminal `outcome` in `values`: the `POST` returns before the first node has run, and for a moment the
thread has a checkpoint but no next node, so a thread polled on `status` alone reads as `done` too early.

```bash
until curl -s localhost:8010/threads/crew:cb-first-1 -H "authorization: Bearer $TOK" \
  | python -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('paused') or (d.get('values') or {}).get('outcome') else 1)"; do sleep 5; done
python scripts/approve.py show crew:cb-first-1
```

A drafting turn takes one to three minutes on the interim door. Every route that stages a proposal pauses,
including the deterministic ones (a known carrier domain or a rule in `config/heuristics.yaml` stages a review
task). `done` without a pause happens in two cases only: personal mail, which is archived with nothing to decide,
and a re-run of a thread whose proposal was already decided (`proposal_already_<status>`). `failed` means every
model call failed, and the thread's `error` and [Operate it](operate.md) say why.

In the output, `interrupt` is the proposal: `kind` (`send_email`, `create_task` or `update_record`), the `payload`
(for a reply: `to`, `subject`, `body`), the `owner_brief` with its citations (`v_renewals.premium`,
`v_renewals.premium_change_pct`, "computed via run_code"), and `compliance_flags`. The client text never carries a
figure; the brief does. `values.memories` above it is what `recall_memory` found for this sender, which is why a
second turn for the same client knows the first.

## Decide

```bash
python scripts/approve.py approve crew:cb-first-1 --by "your name" > /dev/null
curl -s localhost:8010/threads/crew:cb-first-1 -H "authorization: Bearer $TOK" | python -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['values']['outcome'])"
```

```text
done send_email:approved:executed
```

`edit --body "…"` and `reject --reason "…"` are the other two decisions; an edit is re-checked by the compliance gate and flagged if it
quotes a price, but still sent — the human owns the edit.

## See what it did

```bash
docker compose exec -T postgres psql -U producery -d producery -c \
  "select action, ref->>'trace_id' as trace from audit_log where thread_id='crew:cb-first-1' order by id;"
```

Two trace ids: `proposal_staged` is the run, `send_email_executed` is the resume. Open either in LangSmith:
project `producery` → Tracing → paste the id, or filter *Metadata* `thread_id = crew:cb-first-1`. The root run's
metadata names the prompt commits, model tiers, the model door and the build sha that served the turn.

## The same turn in the browser

Open `http://localhost:3010` and sign in as the demo producer `sam` (password `producery-demo`; the committed
roster is for the local stack only). **Connections → Email**: fill the four fields and click **Capture email**;
the card answers `Captured as crew:ui-<hash>` and the worker picks it up within 15 seconds. **Today** shows the
row under *In flight* (`NEW`, then running), then under *Needs you* with a **Decide** button; the card on
**Approve** shows the draft, the owner brief with citation chips and the compliance strip, with **Approve**,
**Edit & approve** and **Reject**; **Thread** links the trace.

Screens: Today, Approve, Thread, Clients, Documents, Connections, Settings. The browser never holds the agent
token; the session, the proxy allowlist and the demo-account guard are in `../design.md` §14.

## What to read next

- [Operate it](operate.md) — reading the trace, failover, resume after a crash, and what a failure means.
- [Work with memory](memory.md) — what this turn wrote, and what the next one recalls.
