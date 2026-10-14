# Friction log — LangChain / LangGraph / LangSmith / Deep Agents

What was awkward in LangChain, LangGraph, Deep Agents and LangSmith while building Producery, for the product team.
Ordered by impact. Most items end with the wish; the rest record a cost without a product ask.

## Platform availability and plans

1. **Beta/optional features are invisible until you probe.** The free org reports `llm_gateway_enabled:
   false`, `sandbox_enabled: false`, `engine_default_enabled: null` only through `GET /api/v1/orgs/current`;
   the Gateway returns a bare `403 "LLM Gateway not enabled for this organization"` with no pointer to how
   or whether it can be enabled on the current plan, and the Gateway admin-setup page never says which plans
   include it. *Wish:* a capability endpoint (and a docs matrix) listing feature × plan × how-to-enable.
2. **Engine's cost model is easy to trip over.** Enabling Engine implies a default 750 LSU/month ($750) cap
   unless an admin sets one first. *Wish:* require an explicit cap at enablement time.
3. **No programmatic surface for automation rules / online evaluators / alerts.** Everything else (datasets,
   experiments, annotation queues, feedback, prompts) is scriptable; rules are UI-only, so "config in the
   repo" for online evals is a declared YAML plus manual clicks. *Wish:* `client.create_rule(...)`.

## Deep Agents

4. **Removing built-ins requires the beta `HarnessProfile` API keyed by provider:identifier.** To give the
   drafter *only* the `task` tool (no filesystem tools, no default general-purpose subagent) you register a
   profile under the exact `"<provider>:<model id>"` key deepagents derives from the pre-built model, using
   private helpers (`deepagents._models.get_model_provider/identifier`) to compute it. *Wish:*
   `create_deep_agent(..., builtin_tools=[], general_purpose_subagent=False)` as plain keyword arguments.
5. **The default general-purpose subagent is opt-out, not opt-in**, and it inherits the parent's tools —
   an easy way to leak a least-privilege allowlist if you don't disable it. *Wish:* opt-in.
6. **`execute` is advertised in the main docstring even when the backend cannot execute** (StateBackend).
   Harmless, but confusing when auditing which tools the model actually sees.

## LangGraph

7. **Static `interrupt_before` + `update_state` needs two resumes.** Injecting the human decision with
   `update_state(as_node=…)` advances the checkpoint and the static interrupt fires again before the node.
   Dynamic `interrupt()` + `Command(resume=…)` is the clean pattern, but the brief's natural phrasing
   ("interrupt_before the consequential tool") pushes people to the static form first. *Wish:* document the
   "inject a value at a static interrupt" pattern explicitly, or let `update_state` suppress one interrupt.
8. **`interrupt()` replays the node from the top on resume**: any side
   effect co-located with `interrupt()` runs twice. We keep the interrupting node pure and stage writes in the
   node before it. *Wish:* a runtime warning when a node that interrupted also performed writes.
9. **No native checkpoint TTL in the open-source Postgres saver.** Retention is an Agent Server feature
   (`checkpointer.ttl` in `langgraph.json`); self-managed deployments prune by hand (`adelete_thread` over
   the `checkpoints` table). *Wish:* TTL in `PostgresSaver`.

## MCP and tools

10. **Dotted tool names break provider tool schemas.** MCP happily serves `sql.query`, but Anthropic/OpenAI
    tool names must match `^[a-zA-Z0-9_-]+$`; the adapter does not warn. Renamed to `sql_query`. *Wish:* a
    warning in `langchain-mcp-adapters` for names that will be rejected by providers.
11. **Trajectory evidence across subagents has no first-class hook.** Tool calls inside subagents live only in
    the trace; to assert `must_call`/`must_not_call` offline we attach a `BaseCallbackHandler` recorder to the
    parent invocation. Works, but every team re-invents it. *Wish:* an `agentevals`-style helper that
    extracts the tool-call trajectory from a run tree (or from the deep agent's result) by default.

## LangSmith SDK

12. **`push_prompt` of identical content raises 409 "Nothing to commit"** (still true in 0.14): the push
    script adopts the latest commit hash on conflict. *Wish:* `allow_unchanged=True`.
13. **Prompt names reject `/`** so namespacing is by prefix (`producery-<agent>`). *Wish:* real namespaces.
14. **The root run id must be minted client-side to join traces to app records.** Passing `run_id` in the
    config works, but nothing in `invoke()`'s return tells you the trace id. *Wish:* return the run id (or
    expose it on the result) from `ainvoke` when tracing is on.
15. **`evaluate()` requires network at gate time**; air-gapped CI needs a parallel file recorder. *Wish:*
    offline experiment artifacts + later upload.

## Docs and tooling

16. **The docs MCP server (`docs.langchain.com/mcp`) was flaky from this network** (TLS resets on roughly
    half the calls) while the reference server worked; `WebFetch` of the same pages also reset intermittently.
    Introspecting installed SDK signatures became the reliable fallback. *Wish:* an `llms-full.txt` bundle
    per product to pin locally.
17. **Windows DX**: psycopg's pure-Python wheel needs `psycopg[binary]`; fastembed's Hugging Face cache warns
    about symlinks on every start; ruff's `ASYNC230/240` flags one-time config reads inside async startup
    paths. All minor, all time.

## Executor, SDK limits and Windows

18. **A sealed executor needs six capabilities, not zero.** Running untrusted snippets as a separate
    unprivileged user from a root daemon under `cap_drop: ALL` required adding `SETUID, SETGID, CHOWN,
    DAC_OVERRIDE, FOWNER, KILL` one failure at a time (chown the run dir, enter it, destroy it, kill the
    timed-out process group). Nothing in the Deep Agents sandbox docs covers the "compose fallback" posture;
    a reference `docker-compose` executor profile would save a day. (LangSmith Sandboxes avoid all of this —
    which is the argument for them.)
19. **`list_runs(limit=…)` silently caps at 100** with a 400 on larger values; the SDK could clamp or page.
20. **`read_run`/`list_runs` are deprecated in 0.14 in favour of `client.runs.retrieve/query`** (SmithDB
    migration) — the deprecation warnings fire on every call; the migration guide is linked but the old
    methods are still the ones most examples show.
21. **Windows long paths break `pip install` of `anthropic`** (a 180-character module name) in deep
    directories; the clean-checkout CI run had to move to a short path.

## Model door, middleware and traces

22. **A CLI-backed model carries its own identity into the protocol.** Claude Code's model intermittently
    answered that the application's `sql_query`/`documents_search` "are not registered MCP tools in this Claude
    Code session" and refused to emit the JSON tool call — even with `--disallowedTools "*"` and a replaced system
    prompt. It took an explicit "these tools belong to the calling application, return the JSON to call them"
    framing to stop it. Any subscription-CLI-as-model setup will hit this; a documented pattern (or a
    first-class OpenAI-compatible serving mode for the CLI) would remove a whole class of flakiness.
23. **`ModelRetryMiddleware` + `ModelFallbackMiddleware` ordering is a silent trap.** With the retry middleware's
    default `on_failure="continue"`, an exhausted primary is turned into an error *message* and the fallback
    middleware never sees an exception, so no failover happens — the trace just shows two errors and a
    degraded answer. `on_failure="error"` with retries inside the fallback is what works. *Wish:* a warning when
    both are present with the swallowing configuration, or a combined `ModelResilienceMiddleware`.
24. **`Runnable.with_fallbacks` results are not `BaseChatModel`s.** Passing a `RunnableWithFallbacks` as
    `create_deep_agent(model=…)` fails deep inside provider-profile resolution (`'ChatOpenAI' object has no
    attribute 'partition'`). The door now returns the primary model and exposes fallbacks separately. *Wish:*
    accept a with-fallbacks runnable, or fail fast with a clear message.
25. **Token accounting through a CLI is inflated:** every call reports 20–45k prompt tokens because the CLI's
    own context rides along; LangSmith cost/usage dashboards built on these numbers overstate by an order of
    magnitude. Flagged in the usage metadata notes.
26. **Dropped trace batches are a client-side warning and nothing else.** When the free tier's ingestion limit is
    exceeded the SDK logs `Failed to multipart ingest runs: LangSmithRateLimitError` and discards the batch; the
    run ids the application already recorded never exist. The app now counts those warnings itself
    (`/ready → traces`). *Wish:* a client callback or counter for dropped runs, and a per-workspace ingestion
    quota endpoint.
27. **MCP transport failures are not tool errors.** `langchain-mcp-adapters` raises an `ExceptionGroup` out of the
    streamable-HTTP client when a server is down; `handle_tool_errors=True` governs only `isError` results, so the
    whole agent run errors. Wrapped every tool to turn it into a structured result. *Wish:* an
    `on_transport_error` policy on the client (raise | structured result).
28. **`pull_prompt` has no offline path.** A Hub outage at startup blocks every restart and deploy of a service that
    pins prompts. Built a fingerprint-verified local fallback. *Wish:* an SDK-side cache keyed by commit hash —
    a pinned commit is immutable, so it is always safe to serve from disk.
29. **No anonymizer on the default tracer.** `Client(anonymizer=…)` exists but LangChain's auto-configured tracer
    builds its own client; masking means passing a `LangChainTracer(client=…)` in every run config. *Wish:*
    `LANGSMITH_ANONYMIZER=<module:callable>` or a project-level masking rule.
30. **Tokens and cost are on the run but not on the experiment row.** `evaluate()` exposes the target's outputs to
    evaluators, not the root run's `total_tokens`/`total_cost`, so a cost metric has to be carried through the
    target's outputs. *Wish:* run usage available to evaluators, and a built-in cost feedback key.

## Evaluation

31. **`with_structured_output` is all-or-nothing on a door that does not enforce schemas.** Through an
    OpenAI-compatible endpoint with no response-schema support, the judge model answered `{"score": 3,
    "rationale": …}` instead of `reason`, and every one of 17 judge calls failed with a pydantic
    `ValidationError`, so a whole experiment recorded evaluator errors instead of scores. The fix was to ask
    for plain JSON and parse it tolerantly. *Wish:* a lenient mode (`strict=False`) that keeps the fields the
    schema can satisfy and reports the rest, or an error that names the keys the model returned.
32. **Multipart trace ingest returns 422 with nothing to act on.** During a 14-row `evaluate()` run on the free
    tier the client logged `Failed to send compressed multipart ingest … 422 Client Error` several times; the
    experiment and the runs still appeared, and nothing said which run or which field was refused. *Wish:*
    the error to name the run id and the rejected part, and a retry policy on 422 for multipart batches.
33. **The SDK gives no URL for a run or an experiment.** Building "Open trace in LangSmith" links in the producer
    UI needs the organization id and the project id copied from the browser (`LANGSMITH_ORG_ID`,
    `LANGSMITH_PROJECT_ID`), and an experiment link needs the dataset id plus `compare?selectedSessions=<session>`;
    `Run`, `TracerSession` and the `evaluate()` result carry none of it. *Wish:* `client.run_url(run_id)` and
    `experiment.url`.
34. **No upsert for dataset examples.** Mirroring a JSONL file into a dataset means listing every example,
    comparing a hash kept in metadata, deleting the changed ones and creating them again, one call each; both
    the repository's sync and the suite runner carry this loop. *Wish:* `upsert_examples(dataset, rows,
    key="metadata.row_id")`.
