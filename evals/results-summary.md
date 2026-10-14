# Results summary

One copy per recorded run, newest first; `python -m evals.evaluators.run_suite` writes the same tables to
`.eval-results/suite-summary.md`, and the raw rows are kept beside this file under `results/`.

## Run 2026-10-14

| Field | Value |
|---|---|
| Date (UTC) | 2026-10-14T17:33:58Z |
| Commit (`git rev-parse --short HEAD`) | `12574c3` (images `022e651`) |
| Model door (`/ready` → `model_door`) | `langchain-interim` (the Claude CLI bridge; every tier one CLI model) |
| Prompt pins (`config/prompts.yaml`) | analyst `eccdae2a`, classifier `bb7d511f`, drafter `be8d0e30`, extractor `76391e1c`, judge_draft `08ec7154` |
| LangSmith experiment | `suite-12574c3-8f6a537a` |
| LangSmith dataset | `producery-suite` |
| Rows / clean / evaluator errors | 29 / 29 / 0 |
| Gate verdict (`python -m producery.evals.gate`, suite file) | GREEN |
| Raw files | [`results/suite-2026-10-14.json`](results/suite-2026-10-14.json), [`results/suite-summary-2026-10-14.md`](results/suite-summary-2026-10-14.md), [`results/gate_report-2026-10-14.md`](results/gate_report-2026-10-14.md) |

## Metrics by category

Targets from `plan.md` section 3. Judges: mean over the rows that carry the judge, no row at 0.

Rows: 29; clean: 29; evaluator errors: 0.

## happy (10 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `cites` | 100 % | 100% (3/3) | pass |
| `classification` | 100 % | 100% (10/10) | pass |
| `compliance_clean` | 100 % | 100% (6/6) | pass |
| `fields` | 100 % | 100% (1/1) | pass |
| `latency` | 100 % | 100% (1/1) | pass |
| `mentions` | 100 % | 100% (3/3) | pass |
| `not_contains` | 100 % | 100% (5/5) | pass |
| `outcome` | 100 % | 100% (10/10) | pass |
| `path` | 100 % | 100% (5/5) | pass |
| `structure` | 100 % | 100% (7/7) | pass |
| `trajectory` | 100 % | 100% (6/6) | pass |
| p95 latency | <= 180 s | 173 s | pass |

## edge (6 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `cites` | 100 % | 100% (2/2) | pass |
| `classification` | 100 % | 100% (6/6) | pass |
| `compliance_clean` | 100 % | 100% (6/6) | pass |
| `latency` | 100 % | 100% (1/1) | pass |
| `not_contains` | 100 % | 100% (6/6) | pass |
| `outcome` | 100 % | 100% (6/6) | pass |
| `recipient` | 100 % | 100% (6/6) | pass |
| `structure` | 100 % | 100% (6/6) | pass |
| `trajectory` | 100 % | 100% (3/3) | pass |
| p95 latency | <= 180 s | 128 s | pass |

## failure (5 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `classification` | 100 % | 100% (5/5) | pass |
| `compliance_clean` | 100 % | 100% (1/1) | pass |
| `error` | 100 % | 100% (1/1) | pass |
| `latency` | 100 % | 100% (3/3) | pass |
| `not_contains` | 100 % | 100% (1/1) | pass |
| `outcome` | 100 % | 100% (5/5) | pass |
| `recipient` | 100 % | 100% (1/1) | pass |
| `structure` | 100 % | 100% (5/5) | pass |
| `trajectory` | 100 % | 100% (4/4) | pass |
| p95 latency | <= 180 s | 153 s | pass |

## redteam (8 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `cites` | 100 % | 100% (1/1) | pass |
| `classification` | 100 % | 100% (7/7) | pass |
| `compliance_clean` | 100 % | 100% (6/6) | pass |
| `fields` | 100 % | 100% (1/1) | pass |
| `injection` | 100 % | 100% (6/6) | pass |
| `mentions` | 100 % | 100% (1/1) | pass |
| `not_contains` | 100 % | 100% (5/5) | pass |
| `outcome` | 100 % | 100% (7/7) | pass |
| `path` | 100 % | 100% (1/1) | pass |
| `recipient` | 100 % | 100% (2/2) | pass |
| `structure` | 100 % | 100% (6/6) | pass |
| `trajectory` | 100 % | 100% (4/4) | pass |
| p95 latency | <= 180 s | 127 s | pass |

## all (29 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `cites` | 100 % | 100% (6/6) | pass |
| `classification` | 100 % | 100% (28/28) | pass |
| `compliance_clean` | 100 % | 100% (19/19) | pass |
| `error` | 100 % | 100% (1/1) | pass |
| `fields` | 100 % | 100% (2/2) | pass |
| `injection` | 100 % | 100% (6/6) | pass |
| `latency` | 100 % | 100% (5/5) | pass |
| `mentions` | 100 % | 100% (4/4) | pass |
| `not_contains` | 100 % | 100% (17/17) | pass |
| `outcome` | 100 % | 100% (28/28) | pass |
| `path` | 100 % | 100% (6/6) | pass |
| `recipient` | 100 % | 100% (9/9) | pass |
| `structure` | 100 % | 100% (24/24) | pass |
| `trajectory` | 100 % | 100% (17/17) | pass |
| p95 latency | <= 180 s | 153 s | pass |
