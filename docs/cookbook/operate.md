# Operate it

> Read a trace like an operator, drill a model failover, resume a thread after a crash, decide a paused run
> from the command line, and find the cause when the stack misbehaves. For whoever runs the stack.

## Is it healthy

```bash
curl -s localhost:8010/ready            # checks{postgres, prompts_pinned, tools, model_door_configured, api_token_configured}; revision_id;
                                        # traces{delivered, ingest_failures, last_error} (dropped trace batches), trace_masking, prompts_source{name: hub|local-fallback}
curl -s localhost:3010/api/health       # the producer UI: 503 names a missing SESSION_SECRET, token or roster
docker compose ps
```

## Read a trace — the metadata join

Every audit row carries the trace id of the turn that wrote it; the trace carries the versions that served it.

```bash
docker compose exec -T postgres psql -U producery -d producery -c \
  "select thread_id, action, ref->>'trace_id' as trace from audit_log where ref ? 'trace_id' order by id desc limit 5;"
```

In LangSmith → project `producery` → the trace:

| Where | What you read |
|---|---|
| root run *Metadata* | `prompts` (five `name:commit`), `models` (three tiers), `model_door`, `revision_id`, `thread_id`, `thread_class`, `agents` |
| root run *Tags* | `producery`, `inbound_crew`, `rev:<sha>` |
| any LLM run *Tags* | `agent:<name>`, `tier:<tier>` — filter the project by `agent:analyst` to see every analyst call |
| `task` tool run | the whole brief the subagent returned; the `analyst` run under it holds its tool calls |

Container logs join on the same ids:

```bash
docker compose logs agent | grep "crew:<email id>"       # memory_written, citations_dropped, run_resume
docker compose logs worker | grep "<email id>"           # inbox_polled, crew_dispatched, job_retry, job_dead_letter
```

## Drill a model failover (interim bridge)

With the interim door, the bridge can be told to fail a model; the declared fallback chain in `config/models.yaml`
takes over without a code change.

```bash
KEY=$(grep -E '^LANGSMITH_API_KEY=' .env | cut -d= -f2-)
curl -s -X POST localhost:8099/admin/fail -H "authorization: Bearer $KEY" -H 'content-type: application/json' -d '{"models":["anthropic/claude-sonnet-5"]}'
python scripts/demo.py run prospect-bakery      # or any client email under a fresh id
curl -s -X POST localhost:8099/admin/fail -H "authorization: Bearer $KEY" -H 'content-type: application/json' -d '{"models":[]}'
```

In the trace: each `anthropic/claude-sonnet-5` LLM run is a 503 error followed by a success on
`anthropic/claude-sonnet-4-6`. Fail every model and a known client's email is marked `failed` after three worker
retries (15 s, 30 s) and dead-lettered — never `done`.

## Resume after a crash

```bash
curl -s -X POST localhost:8010/runs -H "authorization: Bearer $TOK" -H 'content-type: application/json' \
  -d '{"wait":false,"email":{"id":"cb-crash-1","from_email":"sam@example.net","from_name":"Sam Patel","subject":"auto","body":"Does my auto policy renew before the end of the year?"}}'
sleep 15; docker compose restart agent
curl -s localhost:8010/threads/crew:cb-crash-1 -H "authorization: Bearer $TOK" | python -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['next'])"
curl -s -X POST localhost:8010/threads/crew:cb-crash-1/continue -H "authorization: Bearer $TOK" | python -c "import sys,json; d=json.load(sys.stdin); print(d['paused'], d['next'])"
```

`running ['respond']` after the restart means the checkpoint survived the kill; `continue` finishes the turn from
that node and pauses at `apply_decision` with exactly one proposal. The UI shows the same as a **Continue run** button.

## Decide from the command line

```bash
python scripts/approve.py list
python scripts/approve.py show crew:<id>
python scripts/approve.py approve crew:<id> --by "name"       # or: edit --body "…" | reject --reason "…"
```

A second decision on the same thread returns 409 "not paused"; a re-run of an executed email returns
`proposal_already_executed`. Both are the idempotency working.

## Prune and sweep

Checkpoints are pruned daily per thread class (`config/retention.yaml`); run it now:

```bash
docker compose exec -T worker python -c "import asyncio
from arq import create_pool
from arq.connections import RedisSettings
from producery.settings import get_settings
async def m():
    r = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    print('enqueued', bool(await r.enqueue_job('prune_checkpoints', _job_id='manual-prune')))
asyncio.run(m())"
docker compose logs worker --since 2m | grep checkpoints_pruned
```

A pruned thread cannot resume, so a proposal still `pending` on it is flipped to `expired` in the same job
(`proposals_expired=N` in the log line, one `proposal_expired` audit row each). Nothing executes; the Approve screen
stops showing it.

## When something is wrong

| Symptom | Cause and fix |
|---|---|
| `agent` restarts; logs say `AGENT_API_TOKEN` is empty | `.env` has an empty token: run `make token` (or set it), then `docker compose up -d agent worker web` |
| `/ready` → `prompts_pinned: false`, or `prompts-check` fails | the pins in `config/prompts.yaml` are not in your Prompt Hub: run `make prompts-push` once with your `LANGSMITH_API_KEY`, commit the rewritten pins, then restart the agent |
| threads end `failed`; the trace shows `403 LLM Gateway not enabled` or connection errors on every model call | the model door cannot be reached: enable the Gateway, or run `make bridge` and set the three `.env` lines in [Run your first agent turn](first-turn.md#choose-the-model-door); `/ready` → `model_door` tells you which door is active |
| host-side evals or the judge time out (`OpenAITimeoutError`) while the stack is healthy | `host.docker.internal` resolves to a VPN or secondary interface and never reaches the bridge: run host-side evals with `LANGSMITH_GATEWAY_BASE_URL=http://127.0.0.1:8099/v1` (containers keep the `.env` value) |
| `/ready` → `traces.delivered: false` | the LangSmith SDK dropped trace batches; `traces.last_error` names the cause (key scope, endpoint region, the free tier's monthly quota); see [Set up a LangSmith workspace](langsmith-workspace.md) |
| `docker compose up` fails with `IMAGE_TAG is unset` | `.env` predates the pin: add `IMAGE_TAG=local` for a from-source build (the pipeline's `deploy` writes the promoted tag there) |
| `docker compose up` fails with `port is already allocated` | another process holds 8010 / 8091 / 8092 / 8090 / 3010 / 5444: stop it, or change the host side of the port mapping in `docker-compose.yml` |
| image pulls fail with `unauthorized: authentication required` | Docker Hub refused an anonymous pull: the stack pulls library images from `BASE_REGISTRY` (default `public.ecr.aws/docker/library`); set it to a mirror you can reach |
| `web` → `/api/health` 503 | the message names it: `SESSION_SECRET` missing (`make token`), the producer roster not mounted, or the demo account still present behind TLS (`make producer NAME=sam`) |
| sign-in says "Wrong name or password" for a password that matches the roster hash; `docker compose logs web` shows `ENODATA … read` | Docker Desktop's single-file bind mount of `config/producers.yaml` went stale when the file was replaced on the host (a `git checkout` or an editor that writes a new file): `docker compose up -d --force-recreate web` |
| `python: No module named producery` on a host-side command | the venv is not active: `source .venv/Scripts/activate` (see the cookbook [README](README.md#shell-and-windows)) |
| `make: command not found` (Windows) | install GNU make in Git Bash, or run the command under each target in the `Makefile`; CI parses the Makefile with `make -n` on every push |

## What to read next

- [Run and extend the evals](evals.md) — online scoring, the annotation queue, and the test suites.
- [Deploy](deploy.md) — the CD stage, promotion and rollback.
