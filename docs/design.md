# Producery — Design

How the agent system works and why it is shaped this way, for engineers reviewing the design. Companion pages:
`architecture.md` (the diagrams and the components table), `deployment.md` (compose topology and the Kubernetes /
LangSmith Deployment path), `evals.md` (evaluation and recorded results), `pipeline.md` (CI, CD, post).

## 1. Domain and the one workflow

Inbound email for a small P&C insurance agency ("Lakeview Insurance Group", fictional) is triaged, documents
are extracted into structured records, client replies are drafted from the agency's own book of business, and
**every consequential action waits for a licensed producer** (regulated: no premiums, advice, or binding
language in client text). One graph, one entry point.

## 2. Topology — the inbound crew (LangGraph `StateGraph`)

The graph, its thirteen nodes and what each one does are drawn and tabulated once, in `architecture.md` (view 1
and the components table; view 2 for the order of one request); `producery/graphs/crew.py` wires them.

Why `interrupt()` rather than compile-time `interrupt_before`: the brief asks for an interrupt before the
consequential action, resumable from checkpoint. With a static `interrupt_before=["apply_decision"]` the
human's decision would have to be injected with `update_state(as_node=…)`, which creates a new checkpoint
step whose channel versions re-trigger the static interrupt, costing a second resume. LangGraph's dynamic
`interrupt()` + `Command(resume=…)` is the documented way to pause at a point and hand a value back in;
`config/agents.yaml` names `apply_decision` as `graph.hitl_node` (validated at build time), and
`graph.aget_state().interrupts` is what the API and the approval CLI read. A thread with nothing to propose
(a personal email, a deterministic route that staged nothing) skips the pause and goes straight to `remember`.

## 3. Agents, subagents and the doctrine

| Agent | Kind | Reason it exists | Tier | Tools |
|---|---|---|---|---|
| classifier | stage | a workflow stage | fast | none |
| extractor | stage | a workflow stage | worker | none |
| drafter | deep agent | owns judgment for the client reply; **no data tools** | worker | `task` only |
| analyst | subagent | **context isolation + tool-scope isolation**: raw rows/chunks never enter the drafter's context; the only agent with data tools and the only `run_code` grantee (arithmetic over retrieved figures runs in the sandbox, never in a model's head); returns a ≤ 12-line cited brief | worker | `sql_query`, `documents_search`, `run_code` |
| judge | evaluator | anchored 0–3 judge, cross-family; eval-time only | judge | none |

Two agents loop and judge (drafter, analyst); two stage agents are one structured call each with their own
prompt pin, tier and trace tag (classifier, extractor); one evaluator (judge) runs only at eval time.

The analyst is the only `run_code` grantee because arithmetic always follows retrieval: one subagent, one hop. The
`task` → `sql_query` → `run_code` path is a must-pass eval row (`crew-010`); computed figures reach the owner brief
as "computed via run_code", never client text.

Deep Agents is used for `task` delegation and context quarantine only. Planning (`write_todos`) is not part of the
default harness profile in deepagents 0.7.x and is not used; filesystem tools are excluded through the harness profile
— they stay registered in the compiled agent's tool node but are never offered to the model: the drafter sees `task` +
`DraftResponse`, the analyst exactly its three grants. The harness
is kept even with one subagent because native quarantine (the drafter never sees raw rows) and parallel `task` calls
are what it buys.

Subagent descriptions (`scope` in `config/agents.yaml`) are written as API contracts because they are the
`task` routing function. The deepagents default general-purpose subagent and all filesystem tools are
removed through a registered `HarnessProfile` (`excluded_tools`, `general_purpose_subagent.enabled=False`),
so the drafter sees exactly `task` and the subagents see exactly their allowlists. Guardrails are LangChain
middleware, not prose: `ModelRetryMiddleware` (bounded, jittered), `ToolCallLimitMiddleware` (hard cap),
`recursion_limit` per agent. Parallelism: independent `task` calls in one turn fan out (deepagents).

## 4. State channels and reducers (`producery/graphs/state.py`)

| Channel | Reducer | Note |
|---|---|---|
| `email`, `memories`, `classification`, `extraction`, `draft`, `proposal`, `decision`, `outcome`, `error` | last value | scalars/dicts owned by one node at a time |
| `compliance_flags`, `citations`, `path`, `tool_calls` | append (`operator.add`) | trajectory evidence accumulates |
| `artifacts` | keyed merge | small name → reference map (e.g. the memory keys a run wrote); never file bodies |

Checkpoints stay small: email bodies are capped at 8 KB, attachments are document ids, the drafter's own
message list is bounded by deepagents' `SummarizationMiddleware`, and tool outputs are summarized by the
subagents before they reach the parent. Retention per thread class is `config/retention.yaml` (worker cron
prunes through `adelete_thread`; `langgraph.json` carries the same TTL for Agent Server).

## 5. MCP tool catalog (complete)

| Tool | Server | Contract | Tags | Granted to |
|---|---|---|---|---|
| `sql_query(query ≤ 8000)` | mcp-data | `{rows ≤ 200, row_count, truncated, citation}` or `{error:{code,message,hint}}`; one SELECT; allowlisted views only; runs as `producery_readonly` with `statement_timeout` | read-only, deterministic | analyst |
| `documents_search(query 3..500, doc_types?, k 1..10)` | mcp-data | `{hits:[{id, document_id, title, doc_type, section, snippet ≤ 600, score, citation}]}`; hybrid vector + tsvector, each leg normalised and fused 0.6/0.4 (section 12) | read-only, deterministic, retrieval | analyst |
| `run_code(code ≤ 20000, inputs?, timeout_s 1..60)` | mcp-exec | `{stdout ≤ 16 KB, stderr ≤ 4 KB, exit_status, timed_out, duration_ms, artifacts ≤ 4×256 KB, executor}`; a timeout is a result | isolated, compute | analyst |

Errors teach (`relation "households" is not an allowlisted view: v_households, …`; `query must be at least 3
characters`; `code is empty`). No LLM inside any tool. Tools are exposed through FastMCP (streamable HTTP,
stateless) and consumed through `langchain-mcp-adapters` into an explicit registry (`producery/tools/registry.py`);
the factory resolves names from config against the registry and refuses to start on an unknown name.

## 6. Sandboxed execution (`run_code`)

```
agent ──(MCP)──► mcp-exec ──(HTTP, exec_net internal)──► executor
                 holds no secrets                       sealed: internal-only network (no egress), read-only
                 CodeExecutor protocol                  rootfs, tmpfs /work, cap_drop ALL (the daemon keeps six
                 ├ SealedProcessExecutor (compose)      to switch users and clean up), no-new-privileges,
                 │                                      512 MB / 1 CPU / 64 pids; per run: snippet as uid 10002, fresh
                 └ LangSmithSandboxExecutor (K8s/SaaS)  `python -I` subprocess, fresh tmp dir, rlimits, wall
                                                        clock, dir destroyed after
```
The model sees only the tool schema. Swapping to LangSmith Sandboxes is `CODE_EXECUTOR=langsmith` — the
`CodeExecutor` protocol and the tool contract do not change. Sandboxes are not enabled on this account
(`sandbox_enabled: false`), so the sealed executor is the live path.

## 7. Memory architecture

Short-term memory is graph state in the Postgres checkpointer. Long-term memory is the LangGraph
`AsyncPostgresStore` with a pgvector semantic index (`dims=384`, local `bge-small-en-v1.5` embeddings via
fastembed — deterministic, no provider key; an encoder, not an LLM, so it is outside the model door).

| Kind | Namespace | Written by | Policy |
|---|---|---|---|
| semantic | `("memory","semantic",agency,thread_class)` | `remember` after an **executed** `update_record`, and after an approved (or edited and sent) client reply | record updates: facts carry `source` (proposal id) and the extractor's **per-field** confidence; fields below `verify_below_confidence` are not written. Replies: the owner-brief sentences that carry a **kept** citation (`cited_facts`, ≤ 5 per reply, confidence 1.0 — the human decision is the confirmation, `citations ⊆ retrieved ids` is the provenance); never from unexecuted drafts |
| episodic | `("memory","episodic",agency,thread_class)` | `remember` on every terminal outcome | one record per thread with sender, class, outcome, decision, trace id (TTL 180 d, swept by the store's TTL sweeper); the privacy stop withholds the sender |
| procedural | `("memory","procedural",agency,thread_class)` | `remember` only on human **edit** or **reject with reason** | human-confirmed only; recalled into the drafter's volatile block |

The fourth namespace component is the **thread class** — the prefix of the thread id (`crew`, `eval`, `smoke`) — so
evaluation and smoke runs can never write into, or recall from, production memory. Nodes reach the store through
`get_store()`, so the same code runs under Agent Server's platform-injected store.

## 8. Prompt Hub flow and ANCHOR

Five prompts (`prompts/*.yaml`) in ANCHOR order — Assignment, Non-negotiables, Context, How, Output,
References (one worked + one refusal example) — form the stable system prefix; the single volatile human slot
`{{input}}` carries email, recalled memory and task in a fixed order. `scripts/prompts_push.py` pushes to the
Hub and pins commit hashes in `config/prompts.yaml` (the only writer); runtime pulls `name:commit` once at
startup into memory; readiness fails on an unresolvable pin; nothing resolves `latest`. Iterate in the
Playground → commit → re-run `prompts_push.py --check`/push → the eval gate decides promotion. Output contracts
are Pydantic models shared by the prompt's O section and the node's `response_format` (`producery/contracts.py`).

## 9. Model access and failover

`config/models.yaml` is the only place model identifiers exist: tiers `fast`, `worker`, `judge` with a
primary, 1–5 ordered fallbacks and trigger status codes — the shape of a LangSmith LLM Gateway fallback chain.
Application code holds one `ChatOpenAI` client per agent pointed at `LANGSMITH_GATEWAY_BASE_URL` with the
workspace key and an `x-producery-agent` header; assignment is static per agent; promotion happens by editing
the YAML behind the eval gate. The LLM Gateway is not enabled on this organization (`llm_gateway_enabled: false`,
403) and no provider keys exist, so the Claude Code CLI stands in through a host-side bridge (`bridge/claude_cli_bridge.py`) that speaks the
OpenAI chat-completions shape; `MODEL_ACCESS_MODE=langchain` builds one OpenAI-compatible client per chain
member from the same YAML and attaches the declared fallbacks with LangChain's `with_fallbacks` (single
structured calls) and `ModelFallbackMiddleware` (inside the deep agent and subagents) — retries sit inside the
fallback so an exhausted primary fails over. No routing code exists in the app. Traces carry
`model_door_label=claude-cli-bridge (interim, option 3)`. Switching back to the Gateway is two env values. Prompt
caching is by ordering only (the ANCHOR prefix is byte-stable across runs) and unmeasured until the Gateway serves
and reports `cached_tokens`. The judge tier's
cross-family guarantee is likewise a property of the configuration: on the interim bridge every tier is served by
the same CLI, so judge and worker share a family until the Gateway is enabled.

## 10. LangSmith usage map

| Concern | LangSmith capability | Status |
|---|---|---|
| Tracing of every agent, subagent, tool call with metadata (agent@version, prompt commits, model tiers, thread_id, revision_id) | tracing + run metadata/tags; root `run_id` returned as `trace_id` | used |
| Prompts | Prompt Hub, commit-pinned pulls | used |
| Prompt iteration | Playground (needs a workspace model secret to execute) | documented flow |
| Datasets / experiments / regression | datasets with splits + content hash + git sha, `evaluate()`, experiment metadata | used; `evaluate_comparative` for A/B available, not scripted |
| Online scoring | online evaluator rules (declared in `config/langsmith/online_evals.yaml`, created in the UI) + SDK-local deterministic feedback via `create_feedback` | used |
| Human review | annotation queue `producery-review` (created by `scripts/langsmith_setup.py`); low scorers routed in | used |
| Feedback capture | `POST /threads/{id}/feedback` → `create_feedback` on the trace | used |
| Dashboards | built-in project monitoring (latency, tokens, cost, feedback) | used; custom dashboard spec not scripted |
| Alerts | thresholds in repo; `online.py` exits 2 on breach; LangSmith alert rules mirror thresholds | config + documented |
| LLM Gateway | single model door, fallback chains, spend policies | **target; flagged (not enabled on org)** |
| Sandboxes | `run_code` executor | **target; compose fallback (not enabled on org)** |
| Engine, Insights, Chat, Fleet, SmithDB | not used: plan or entitlement, or the agents are built in code; the manual failure→golden-row loop (`scripts/mint_golden.py`) stands where Engine would | not used |
| LangSmith Deployment / Agent Server | serving runtime | packaged via `langgraph.json`; compose runs FastAPI (standalone server needs a license) |

## 11. Observability and evals (summary)

Every run stamps `metadata` from `CrewDeps.trace_metadata()`; node runs carry `run_name` and `tags`
(`agent:<name>`); the drafter subgraph carries the deepagents integration metadata. `docs/evals.md` covers
datasets, evaluators (deterministic first; judge cross-family and anchored), trajectory asserts, gates,
online scoring and the loop.

## 12. Background worker and semantic search

`arq` over Redis: `ingest_document` (chunk → embed → `document_chunks` with vector + tsvector), `poll_inbox`
(cron, claims `new` emails with `FOR UPDATE SKIP LOCKED`, enqueues `run_crew`), `run_crew` (posts a **background**
run to the agent — `POST /runs {wait:false}` → `202` — and polls the inbox status; idempotent by thread id; a failed
or timed-out turn raises `arq.Retry` with 15 s / 30 s backoff, three tries, then exactly one `job_dead_letter` audit
row), `prune_checkpoints` (retention; a pruned thread's still-pending proposal is flipped to `expired` with a
`proposal_expired` audit row, so the Approve screen never shows a thread that can no longer resume).
Search is hybrid: cosine over `vector(384)` and `ts_rank_cd` over `websearch_to_tsquery`, each leg normalised to its
own best hit and fused 0.6/0.4 into a 0–1 score (reciprocal-rank fusion did not separate
neighbouring chunks); hits carry `[doc:<chunk id>]` citations; a query under three characters is a teaching error
(`query_too_short`), not a schema rejection.

## 13. External integrations

Only LangSmith (tracing, Hub, datasets, feedback, annotation queues) and the model door. Email, Drive/OneDrive,
telephony, Telegram, AMS and billing integrations of the source platform were not ported (non-goals).

Not built: document ingestion as an agent. Ingestion runs as a deterministic worker job; an ingestion subagent
fanning out over synced folders with `Send` would be the next candidate, at the cost of a second trust boundary and
the OAuth registrations.

## 14. Security and failure boundaries

| Boundary | Mechanism | Where |
|---|---|---|
| Agent API | bearer token `AGENT_API_TOKEN` (or the same value in an `x-producery-token` header) on every route except `/health` and `/ready`; the app refuses to start without it; worker, approval CLI, demo and the eval target present it | `producery/server/app.py` `require_token` |
| Secrets per service | every service has an explicit `environment:` list: the executor sees two variables (`EXEC_MAX_TIMEOUT_S`, `EXEC_WORK_ROOT`), `mcp-exec` the executor URL and settings, `mcp-data` the database and embedding settings, `seed` the database and queue URLs, `web` the agent token and the session secret, agent and worker the application variables; the LangSmith key reaches agent and worker only, the session secret reaches web only | `docker-compose.yml` |
| Tool servers | `documents_search` and `sql_query` both run under `producery_readonly` (`fetch_as_role`) | `producery/mcp_data/server.py` |
| Interim bridge | `BRIDGE_TOKEN` required on completions and the drill endpoint (the key the app already presents) | `bridge/claude_cli_bridge.py` |
| Honest failure | a turn whose model calls all fail → inbox `failed`, `turn_failed` audit with the trace id, `503` to a waiting caller; the worker retries with backoff and dead-letters | `app.py` `_finish_email`, `worker/main.py` `_fail` |
| Degradation | classifier fallback routes to `task_proposal` (human triage), never to the deep agent | `nodes.py` `route_after_classify` |
| Human edits | the edited payload re-enters the DLP gate before execution; flags → `edited_draft_flagged` audit (recorded, not blocked — the human owns the edit) | `nodes.py` `execute` |
| Re-ingestion | `stage_proposal` refuses to re-stage a thread whose proposal already executed/rejected (`proposal_already_<status>`) | `nodes.py` `stage_proposal` |
| Build identity | `REVISION_ID` is a build arg baked into the image; it is not read from `.env` | `Dockerfile`, `docker-compose.yml` |
| Authorization granularity | one service token for every route; reviewer identity is `Decision.by` (free text, overwritten by the web proxy with the signed-in producer) until an authenticating gateway in front supplies it | `producery/server/app.py`, `contracts.py` |
| One run per thread | `run_claims` row taken before a run, released after; a concurrent `POST /runs` for the same thread gets 409 (stale claims expire after 15 min) | `records.claim_thread`, `app.start_run` |
| Producer UI | the browser holds only an encrypted session cookie (iron-session, `SESSION_SECRET`); the Next.js server keeps `AGENT_API_TOKEN`, allowlists method + path on `/api/ui/*`, overwrites `by` on every resume with the session's producer and sends `x-producery-user`; accounts are bcrypt hashes in `config/producers.yaml` (no sign-up, no admin); 10 failed sign-ins per name per minute | `web/src/app/api/ui/[...path]/route.ts`, `web/src/lib/{session,producers,api}.ts` |
| Demo roster guard | the committed roster's demo hash is refused at readiness and sign-in when `COOKIE_SECURE=1` (a TLS deployment) until `make producer` rotates it; the roster is mounted, never baked into the image | `web/src/lib/producers.ts` `demoAccountBlocked`, `web/scripts/rotate-producer.mjs` |
| UI integration layer | read endpoints `/inbox`, `/proposals`, `/outbox`, `/book/{view}` (allowlisted views only, `fetch_as_role`), `/documents`, `/documents/search` (the analyst's own tool), `/connections`; the only writes are manual intake — `POST /inbox` (pasted email → `new`, the worker handles it like any sensor message) and `POST /documents` (upload → `ingest_document`) — plus `PUT /connections/{provider}` restricted to `manual`; all under the same bearer token | `producery/server/ui_api.py` |
| Hidden-but-registered tools | excluded Deep Agents tools remain in the tool node but are never offered to the model | `agents/factory.py` `_EXCLUDED_BUILTINS` |
| Tool-service outage | a transport failure to `mcp-data` / `mcp-exec` becomes a structured `tool_unavailable` result (the analyst reports "data unavailable", the turn completes and pauses with the admission in the brief) instead of an erroring turn; the recorder counts it as a failed call for `tool_failure_free` | `tools/registry.py` `degrading` |
| Hub outage at startup | a pull failure falls back to `prompts/<name>.yaml` only when its fingerprint equals the pin's; `/ready` reports `prompts_source: local-fallback`; a drifted file still refuses to start | `prompts/hub.py` `_local_fallback` |
| Trace delivery | the SDK's dropped-batch warnings (free-tier rate limit, 5xx) are counted; `/ready` → `traces.{delivered, ingest_failures, last_error}`; online scoring alerts on `trace_delivery < 1.0` | `observability/trace_delivery.py`, `evals/online.py` |
| Client text in traces | one masked LangSmith client for every trace and feedback call: e-mail local parts, phone numbers and SSN shapes redacted, sender names masked by field, domains kept (`LANGSMITH_MASK_PII`); minted golden rows are scrubbed by default (`--raw` opts out) | `observability/masking.py`, `evals/scrub.py` |
| Retention statement | app stores: checkpoints 30/2/1 d by class (prune job), episodic memory 180 d, semantic/procedural kept, proposals `expired` with their checkpoint; LangSmith: traces masked client-side, retention is the workspace's policy (on the Part 1 self-hosted platform it is yours to set); repo: golden rows pseudonymous | `config/retention.yaml`, this row |
| Image identity | `IMAGE_TAG` has no default in compose; `pipeline.py deploy`/`rollback` pin it in `.env`, so an untagged `docker compose up -d <service>` can no longer switch the stack to another image | `docker-compose.yml`, `scripts/pipeline.py` `pin_image_tag` |
| Schema evolution | one idempotent `schema.sql` (create-if-not-exists, constraint swap) applied by `seed`; numbered, reversible migrations are deferred until the first production schema change | `producery/store/schema.sql` |
