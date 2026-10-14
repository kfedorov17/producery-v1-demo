# Run and extend the evals

> Four datasets in the repository, mirrored to LangSmith on every run; deterministic checks first, a judge on two
> rows; a gate with no skip flag that proves it can go RED. The loop: a wrong trace becomes a golden row. For
> whoever changes an agent, a prompt or a tool; the last section is for whoever runs the test suites.

## Run

```bash
python -m producery.evals.run --all --prefix cb            # four experiments in LangSmith, results in .eval-results/
python -m producery.evals.gate                             # per-dataset GREEN / RED and the reasons
head -20 .eval-results/gate_report.md                      # the report: verdict, experiment ids, every row's scores
```

Smaller runs while iterating:

```bash
python -m producery.evals.run crew_e2e --rows crew-001 --local   # one row, file recorder only (no LangSmith experiment)
python -m producery.evals.run --suite smoke                      # the 6-row smoke set the CD stage uses
```

## Read an experiment

LangSmith → Datasets → `producery-crew_e2e` → Experiments → the id printed by the run. Each row shows the
deterministic keys (`classification`, `outcome`, `not_contains`, `compliance_clean`, `cites`, `trajectory`,
`schema_valid`, `latency`, `injection` on red-team rows) and `judge` (0–3) on the two judged rows; click a row to open
the trace of that evaluation run. Thresholds live in `config/eval_gates.yaml` (G1 must-pass, G5 red-team, G7 p95
latency, …).

## Prove the gate can fail

```bash
python scripts/pipeline.py prove-gate                      # RED on the committed proof rows → "the gate can fail (OK)"
```

The CI stage runs this before the real suite, every time.

## Add a golden row from a trace

Pick the trace that was wrong (or right, and worth keeping). The script copies the inputs from the trace, writes the
expected outputs you name, and refuses to mint the same trace twice.

```bash
python scripts/mint_golden.py --trace <trace id> --expect-class client_service \
  --expect-outcome send_email:approved:executed --tag validation --note "why this row exists"
python -m producery.evals.run crew_e2e --rows golden-<first 8 of the trace id>
```

The row lands in `evals/datasets/crew_e2e.jsonl` with `source_trace_id`, `split: minted` and the default checks
(`classification_correct`, `compliance_clean`, `outcome_equals`, and `schema_valid` on the send_email route). Add `trajectory` or
`cites_approved` checks by editing the row — `crew-009` and `crew-010` are the examples to copy.

The sender is scrubbed on the way in: a deterministic `client-<hash>@<same domain>` address and a fixture name,
replaced wherever they appeared in the subject and body; phone numbers and SSN shapes are redacted. The domain
survives because the heuristics route on it. `--raw` keeps the trace text verbatim — for synthetic fixtures only;
the row's metadata records `scrubbed: true|false`.

## Write a row by hand

```json
{"inputs": {"decision": "approve", "email": {"id": "crew-012", "from_email": "sam@example.net", "from_name": "Sam Patel", "subject": "roof", "body": "Our roof was replaced last month — does that change my renewal?"}},
 "outputs": {"classification": "client_service", "outcome": "send_email:approved:executed", "must_not_contain": ["$", "premium will"]},
 "metadata": {"row_id": "crew-012", "checks": [{"name": "classification_correct"}, {"name": "outcome_equals"}, {"name": "not_contains"}, {"name": "compliance_clean"}, {"name": "trajectory", "args": {"must_call": ["task", "sql_query"], "ordered": true, "max_steps": 8}}], "tags": ["must-pass", "draft-route"]}}
```

`row_id` must be unique; `row_hash` and `dataset_hash` are computed on the next run and written to the LangSmith
example's metadata, so a changed row is a new example, never a silent edit.

## Online scoring

```bash
python -m producery.evals.online --hours 24 --sample 1.0
```

Scores production traces (`thread_class crew` only) with `online.grounding`, `online.tool_failure_free`,
`online.compliance_clean`, `online.latency_ok` (and `online.data_unavailable` when a brief admits it); a breach
exits 2, prints `ALERT:` lines and pushes the runs to the annotation queue `producery-review`.

It also reads the agent's `/ready` and scores `trace_delivery`: 1.0 when the LangSmith SDK has dropped no trace
batch since the agent started, 0.0 otherwise (`alert_below: 1.0`): the alarm for audit rows whose trace ids never
reached LangSmith (the free tier's ingestion limit). `/ready` → `traces.last_error` names the cause.

Every crew row also carries `tokens` and `cost_usd` feedback (the agent's per-model usage priced from
`config/models.yaml` `pricing`); the gate's G9 holds the mean cost per row under a ceiling and within +25 % of the
baseline. On the interim CLI bridge the estimate is an upper bound (the CLI's own prompt rides along).

## Run the test suites

```bash
make lint                        # ruff check + ruff format --check (docstrings included)
make typecheck                   # mypy over producery/
make test                        # the Python unit suite: no network, seconds (one POSIX rlimit test skips on Windows)
npm --prefix web test            # the web unit suite (vitest)
npm --prefix web ci && npx --prefix web playwright install chromium   # once, before the first e2e run
make web-e2e                     # Playwright against the running stack (web on :3010); 11 specs
python scripts/pipeline.py ci    # the whole CI stage exactly as GitHub Actions runs it (needs Docker; ~25 minutes with the door)
```

`make evals` is the four eval datasets through `langsmith.evaluate` followed by the gate (needs the model door,
about 15 minutes); `make smoke` runs the six-row golden subset the CD stage uses against the running stack. The
gate writes `.eval-results/gate_report.md` (gitignored); to record a verdict, copy it to `evals/results/` with the
date in the name and commit that copy.

Variables the web suites read: `WEB_URL` (default `http://localhost:3010`), `E2E_USER` and `E2E_PASSWORD` (the
demo producer), `E2E_LIFECYCLE=1` to run the two model-dependent specs on a CI host, `NEXT_STANDALONE=1` for the
standalone build the image uses.

## What to read next

- [Change a prompt](change-a-prompt.md) — the fix that usually follows a failing row.
- `../evals.md` — evaluator definitions and the gate's thresholds in full; `../pipeline.md` — every CI step.
