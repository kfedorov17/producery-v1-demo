# Set up LangSmith for this stack — from sign-up to the first delivered trace

> Use this when you start from a new LangSmith account, move the stack to another organization, or the free tier's
> monthly trace quota is exhausted (`/ready → traces.delivered false`, `last_error` says "Monthly unique traces usage
> limit exceeded"). Everything the application needs in LangSmith is created by two repo commands; the UI part is a
> sign-up, one API key and two ids.

## Step 1 — Sign up

1. Open `https://smith.langchain.com` → **Sign up** (e-mail, Google or GitHub). Verify the e-mail if asked.
2. The first sign-in creates a **personal organization** on the free Developer plan. It has **exactly one
   workspace** — there is no Workspaces menu and nothing to create; the organization is the workspace, and every
   project, dataset, prompt and API key lives in it. (More than one workspace exists only in shared organizations
   on paid plans.)

## Step 2 — Create the API key

1. **Settings → API Keys → + Create API Key**.
2. Type **Service key** — not a personal access token. A service key belongs to the workspace and keeps working
   if a person leaves; it is what the stack and CI hold. Name it `producery-stack`.
3. Copy it now; it is shown once. It starts with `lsv2_sk_` (a personal token starts with `lsv2_pt_`).

## Step 3 — Note the organization id

The UUID after `/o/` in any LangSmith URL while you are signed in (also Settings → Organization). The producer UI
uses it to build "Open trace in LangSmith" links.

## Step 4 — Put the key and ids in `.env`

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_sk_…                            # step 2; never commit it; the bridge uses it as BRIDGE_TOKEN too
LANGSMITH_PROJECT=producery                            # the tracing project name; step 5 creates it
LANGSMITH_ENDPOINT=https://api.smith.langchain.com     # EU-hosted account: https://eu.api.smith.langchain.com
LANGSMITH_MASK_PII=true
LANGSMITH_ORG_ID=<organization uuid>                   # step 3
LANGSMITH_PROJECT_ID=                                  # filled in step 6
```

The key is read by the agent and the worker (compose passes it to those two services only), by the host-side
scripts and evals, and by `bridge/claude_cli_bridge.py`. The seed job, the MCP tool services, the executor, the web
UI and postgres never see it.

## Step 5 — Create what the application expects (two commands, idempotent, in this order)

```bash
make prompts-push
```

Pushes `prompts/*.yaml` to this workspace's Prompt Hub and rewrites `config/prompts.yaml` with the new commit hashes.
Prompt commits are per workspace: the agent pulls each prompt by its pinned commit, and in a new workspace the old
pins do not exist until you push. **Commit the changed `config/prompts.yaml`** (only the commit hashes change; the
content fingerprints stay the same) — otherwise CI's `prompts-check` fails and another checkout would pull foreign pins.

```bash
make langsmith-setup
```

Creates the tracing project and the annotation queue, mirrors the four datasets, and prints the two rules only the
UI can create (step 8). Expected:

```text
project created: producery
annotation queue created: producery-review (…)
dataset synced: producery-classifier (+7 ~0 -0)
dataset synced: producery-crew_e2e (+14 ~0 -0)
dataset synced: producery-extractor (+4 ~0 -0)
dataset synced: producery-tools (+10 ~0 -0)
```

Running either command again is safe: unchanged prompts are reused, existing objects are reported as existing.

## Step 6 — Note the project id

LangSmith → **Tracing → `producery`**: the UUID after `/projects/p/` in the URL. Put it in `LANGSMITH_PROJECT_ID`
in `.env`.

## Step 7 — Restart the services that read the key, and verify

```bash
docker compose up -d agent worker web
```

```bash
python scripts/pipeline.py wait-ready
```

```bash
curl -s localhost:8010/ready | python -m json.tool
```

Read: `"prompts_source"` all `"hub"` (the new pins resolved) and `"traces": {"delivered": true, "ingest_failures": 0}`.
Then one turn:

```bash
TOK=$(grep -E '^AGENT_API_TOKEN=' .env | cut -d= -f2-); curl -s -X POST localhost:8010/runs -H "authorization: Bearer $TOK" -H 'content-type: application/json' -d '{"email":{"id":"ws-check-1","from_email":"sam@example.net","from_name":"Sam Patel","subject":"renewal","body":"What changed on my homeowners renewal this year?"}}' | python -c "import sys,json; d=json.load(sys.stdin); print('paused' if d.get('paused') else d.get('status'), d.get('trace_id'))"
```

Open LangSmith → Tracing → `producery`: the root run `inbound_crew` with that trace id is there; its metadata
carries `prompts.* = <hub name>:<new commit>`; the sender in its inputs reads `***@example.net` (masking). If the run
is missing, `/ready → traces.last_error` names the cause (key scope, endpoint region, quota).

## Step 8 — The two things only the UI can do

Declared in `config/langsmith/online_evals.yaml`; the SDK cannot create rules, so create them under
Tracing → `producery` → **Evaluators → + Evaluator**:

- `draft-quality-judge`: LLM-as-judge on runs named `drafter`, Hub prompt `producery-judge-draft`, feedback key
  `judge`, sampling 0.2.
- `low-score-to-annotation-queue`: automation that adds `inbound_crew` runs with `judge < 2` to the
  `producery-review` queue.

`python -m producery.evals.online` scores and alerts SDK-side without them; they add in-platform judge sampling.
Optional: Tracing → project → **Alerts** mirroring the YAML thresholds for e-mail or webhook alerts.

## Step 9 — CI

GitHub → repository **Settings → Secrets and variables → Actions → `LANGSMITH_API_KEY`** = the new service key.
The workflow writes it into `.env` on the runner. Hosted runners have no model door, so their eval step runs the
model-free `tools` dataset only; the prompt check and the dataset sync use the key.

## Step 10 — Verify end to end

Re-run the checks of step 7 (`/ready` → `prompts_pinned true`, `traces.delivered true`, then one turn as in
[Run your first agent turn](first-turn.md)). With `traces.delivered true`, every experiment `make evals` names in
the gate report is in this workspace, and every thread's trace link resolves.

---

## If you are here because the trace quota ran out

The Developer plan allows 5,000 traces a month per personal organization. Past that the SDK gets HTTP 429 on
ingestion and the agent reports it on `/ready`. The window is the calendar month, UTC. The options:

| Option | What it costs | What you get |
|---|---|---|
| wait for the first of the month (UTC) | nothing | the same workspace, keys and pins, a fresh 5,000; until then the stack keeps running and `/ready` counts the dropped batches, but new threads have no trace to open |
| Settings → Usage and Billing → add a payment method | pay-as-you-go beyond 5,000 (pricing page) | the cap lifts immediately in the same workspace; nothing in the stack changes; set a **usage limit** on the same screen so a runaway eval cannot bill you |
| a new organization (top-left org switcher → New organization) | a shared organization is the Plus plan, paid after its trial | a separate workspace with its own allowance — then steps 2–10 above |
| a separate LangSmith account (another e-mail) | nothing | another personal organization with its own 5,000; a different user — then steps 2–10 above |

## What to read next

- [change-a-prompt.md](change-a-prompt.md) — the push / pin / gate loop you ran once in step 5.
- [evals.md](evals.md) — datasets, experiments and online scoring in the workspace.
- `../design.md` §10 — the LangSmith usage map.
