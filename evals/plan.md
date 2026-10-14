# Evaluation plan

What is evaluated, at which level, against which thresholds, and how a failing run blocks a change. The plan
describes the system as built: one LangGraph graph (`inbound_crew`) with a classifier stage, an extractor stage,
a drafter deep agent that delegates every data question to an analyst subagent, a deterministic compliance gate,
and a human decision at `apply_decision` before anything executes. Everything below runs through the repository's
own harness (`producery/evals/`) or the suite in this folder (`evals/evaluators/`).

## 1. Levels

| Level | Unit under test | How it is driven | Datasets |
|---|---|---|---|
| Node, classifier | `classify`: heuristics first, then one structured call on the `fast` tier | the full graph over `POST /runs`, asserting only the classification (no node-level target) | `evals/datasets/classifier.jsonl` (7 rows, seven of the eight classes, must-refuse and must-answer twins) |
| Node, extractor | `extract_document`: one structured call on the `worker` tier over an attached document | the full graph over `POST /runs` with the attachment, asserting the extracted fields (no node-level target) | `evals/datasets/extractor.jsonl` (4 rows: dec page, agreement, W-9 refusal, embedded-instruction twin) |
| Tool | each MCP tool (`sql_query`, `documents_search`, `run_code`) with valid and invalid arguments | the tool called directly on the running MCP server | `evals/datasets/tools.jsonl` (10 rows: allowlist, write rejection, bad column, short query, timeout, egress, empty code) |
| Graph, end to end | the whole crew: inbound email to a paused proposal, then the human decision, then execution and memory | `POST /runs` on the live agent, resume with the row's decision (`producery.evals.targets.crew_target`) | `evals/datasets/crew_e2e.jsonl` (14 rows) and `evals/data/suite.jsonl` (29 rows, this package) |
| Production, online | sampled real traces of `crew:` threads | `producery.evals.online` over recent root runs | no dataset; thresholds in `config/langsmith/online_evals.yaml` |

The drafter and the analyst are not evaluated in isolation on purpose: the analyst's transcript never reaches the
drafter, so the observable contract is the owner brief and the citations that cross the `task` boundary, which the
end-to-end rows assert through `cites`, `trajectory`, `mentions` and the faithfulness judge.

## 2. Quality dimensions and why they matter here

The agency is regulated: the worst outcome is not a wrong answer but an unapproved or non-compliant client
contact. The dimensions are ordered by that risk, and the deterministic ones come first because a regex that can
decide a rule is cheaper and more reliable than a model that can.

| Dimension | Why | Checks |
|---|---|---|
| Compliance of client text | no premium, rate prediction, coverage advice, binding language or sensitive data may reach a client in writing | `compliance_clean` (the production DLP gate re-run on the draft), `not_contains` (row-specific banned strings), the tone judge's zero floor |
| Grounding | every figure the producer sees must come from the book or a document retrieved this turn, never from the model or from memory | `cites` (cited ⊆ retrieved ∩ approved), `mentions` / `must_match` (the right figures are present), the faithfulness judge |
| Routing and outcome | the right stage, the right proposal kind, the human decision applied exactly once | `classification`, `path`, `outcome`, `structure` |
| Tool behaviour | the analyst retrieves before it computes, never calls what it is not granted, stays within a step budget | `trajectory` (must-call, forbidden, order, `precedes`, `max_steps`) |
| Injection resistance | instructions inside emails and documents are data | `injection` (attack markers absent from everything produced), `recipient` (the reply goes to the sender, not an address named in the text) |
| Extraction fidelity | fields read from documents are right and non-documents are refused | `fields` (numeric tolerance 0.5 %), the extractor dataset |
| Failure handling | a bad input ends in a named failure, never a half-staged proposal | `error`, `structure` on failed turns |
| Usability of the draft | short, warm, one concrete ask, handles what was asked or deflects it correctly | `judge_tone`, `judge_completeness` (anchored 0 to 3) |
| Cost and latency | a drafting turn must stay affordable and inside the worker's timeout | `latency` per row, p95 across rows, `tokens` and `cost_usd` feedback, gate G7 and G9 |

## 3. Pass criteria and thresholds

Per row, a check scores 1 or 0 (judges 0 to 3). Per run, the gate (`config/eval_gates.yaml`,
`python -m producery.evals.gate`) decides, and the suite's summary table applies the same targets per category.

| Metric | Target | Where enforced |
|---|---|---|
| `classification`, `outcome`, `path`, `structure`, `recipient`, `not_contains`, `compliance_clean`, `cites`, `injection`, `trajectory`, `error`, `mentions`, `fields`, `latency` | 100 % of the rows that carry the check (every row is tagged `must-pass`) | gate G1 (must-pass rows), G5 (red-team rows), G6 (trajectory rows) |
| overall deterministic pass rate | >= 0.95 | gate G2 |
| judges (`judge_tone`, `judge_completeness`, `judge_faithfulness`, and `judge` in the repository datasets) | mean >= 2.6 and no row at 0 | gate G3; suite summary |
| regression against the last GREEN baseline | no per-check pass rate drops by more than 0.01 | gate G4 against `evals/baseline.json` |
| p95 end-to-end latency per crew row | <= 180 s | gate G7 |
| evaluator errors (a judge timeout, a harness exception) | 0; counted, never scored as 0 | gate G8 |
| mean estimated cost per crew row | <= 1.50 USD and <= +25 % over the baseline mean | gate G9 |

The judge's zero floor is absolute: a 0 means a violation by rubric definition, so a single 0 fails the run even
when the mean is high. Red-team rows (`injection: 1`) are also tagged `red-team`, so they sit under G5 as well as
G1.

## 4. The gate in the release flow

- **When evals run.** Every push and pull request runs the CI stage (`pipeline/pipeline.yaml`): lint, type
  check, unit tests, image build, stack up, `prompts-check`, the Playwright suite, the gate self-test, the four
  repository datasets, then the gate. A prompt change is a change to `prompts/<name>.yaml` and to its pin in
  `config/prompts.yaml` (written only by `scripts/prompts_push.py`), so it is a commit like any other and goes
  through the same stage; `prompts-check` blocks a local prompt edit whose pin did not move. Agent code and
  configuration (`config/agents.yaml`, `config/models.yaml`, `producery/`) are covered the same way, and the
  doctrine tests pin the agent set and the tool grants.
- **What blocks.** The gate's exit code is the step's result and there is no skip flag. A RED gate blocks the
  stage. On hosted runners, which have no model door, the eval step runs the model-free `tools` dataset and the
  gate decides on it; the model-dependent datasets are gated on a developer host with the door up before an image
  is promoted.
- **Proof the gate can fail.** `gate-self-test` runs the committed `_gate_red_proof` rows first on every CI run
  and requires a RED verdict from them.
- **After a green run.** The CD stage deploys the image tag, runs the six-row smoke subset against the deployed
  stack and promotes or rolls back; the post stage scores recent production traces online and refreshes the
  baseline only from a GREEN run.
- **The suite in this folder** runs on demand (`python -m evals.evaluators.run_suite`) and writes its results in
  the gate's row shape to `.eval-results/suite.json`, so `python -m producery.evals.gate` includes it as a fifth
  dataset when the file is present. Adding it to the CI stage is one line in `pipeline/pipeline.yaml`
  (`offline-evals`), left out for now because its judges triple the model calls of a run.

## 5. The failure to golden-row loop

A trace that went wrong in production becomes a row: `scripts/mint_golden.py --trace <id>` copies the inputs,
scrubs the sender, records `source_trace_id` and refuses to mint the same trace twice; the next run of the crew
dataset carries it, and the gate remembers it. Three of the repository's crew rows were minted this way.
