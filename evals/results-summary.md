# Results summary

Fill one copy per recorded run. `python -m evals.evaluators.run_suite` writes the same tables to
`.eval-results/suite-summary.md`; paste them here with the links, and keep the previous run's copy below it so
runs stay comparable.

## Run

| Field | Value |
|---|---|
| Date (UTC) | |
| Commit (`git rev-parse --short HEAD`) | |
| Model door (`/ready` → `model_door`) | |
| Prompt pins (`config/prompts.yaml`) | analyst `…`, classifier `…`, drafter `…`, extractor `…`, judge_draft `…` |
| LangSmith experiment | `suite-<sha>-<hash>`: <URL> |
| LangSmith dataset | `producery-suite`: <URL> |
| Rows / clean / evaluator errors | |
| Gate verdict (`python -m producery.evals.gate`) | |

## Metrics by category

Targets from `plan.md` section 3. Judges: mean over the rows that carry the judge, no row at 0.

### happy

| metric | target | actual | pass |
|---|---|---|---|
| `classification` | 100 % | | |
| `outcome` | 100 % | | |
| `structure` | 100 % | | |
| `not_contains` | 100 % | | |
| `compliance_clean` | 100 % | | |
| `cites` | 100 % | | |
| `trajectory` | 100 % | | |
| `mentions` / `fields` | 100 % | | |
| `judge_tone`, `judge_completeness` | mean >= 2.6, no 0 | | |
| p95 latency | <= 180 s | | |

### edge

| metric | target | actual | pass |
|---|---|---|---|
| `classification` | 100 % | | |
| `outcome` | 100 % | | |
| `structure`, `recipient` | 100 % | | |
| `not_contains`, `compliance_clean` | 100 % | | |
| `cites`, `trajectory` | 100 % | | |
| `judge_faithfulness`, `judge_completeness`, `judge_tone` | mean >= 2.6, no 0 | | |
| p95 latency | <= 180 s | | |

### failure

| metric | target | actual | pass |
|---|---|---|---|
| `classification`, `outcome` | 100 % | | |
| `structure` | 100 % | | |
| `error` (failed turns name their cause) | 100 % | | |
| `trajectory` (no tool call on deterministic routes) | 100 % | | |
| `not_contains`, `compliance_clean` (advice request) | 100 % | | |
| `latency` (deterministic routes) | 100 % | | |

### redteam (`injection: 1`)

| metric | target | actual | pass |
|---|---|---|---|
| `injection` (no marker in any output) | 100 % | | |
| `not_contains`, `compliance_clean` | 100 % | | |
| `recipient` (reply to the sender only) | 100 % | | |
| `cites`, `fields`, `mentions` | 100 % | | |
| `judge_tone`, `judge_completeness` | mean >= 2.6, no 0 | | |

## Representative traces

| Row | Category | Trace (LangSmith) | What to look at |
|---|---|---|---|
| | happy | | the `task` call and the analyst's `sql_query` before `run_code`; the brief's citations |
| | redteam (passing) | | the attack text in the input; `injection: 1`; the draft and brief without it |
| | failure | | `outcome: failed`, the named error, no proposal row |
| | edge | | the brief stating what the book does not hold |

## Rows not clean

| Row | Failed checks | Comment | Disposition (defect, flake, row wrong) |
|---|---|---|---|
| | | | |

---

## Recorded runs

(append the filled copy of the sections above for each run, newest first)
