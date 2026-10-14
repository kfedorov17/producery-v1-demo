# Evaluation package

The evaluation plan, the suite dataset, the evaluators and the runner for the inbound crew, beside the datasets
the repository's own harness runs. Everything here runs against the live stack through the same target the
repository uses (`producery.evals.targets.crew_target`: `POST /runs`, resume with the row's decision), and writes
results in the shape the repository's gate reads.

| Path | What it is |
|---|---|
| [`plan.md`](plan.md) | the evaluation plan: levels, quality dimensions, thresholds, how the gate fits the release flow |
| [`data/suite.jsonl`](data/suite.jsonl), [`data/README.md`](data/README.md) | the 29-row suite (happy, edge, failure, red-team with `injection: 1`) and its schema and coverage |
| [`evaluators/deterministic.py`](evaluators/deterministic.py) | 14 deterministic checks selected by a row's expected properties |
| [`evaluators/judges.py`](evaluators/judges.py) | three LLM judges (faithfulness, tone, completeness), anchored 0 to 3, rubric in the prompt |
| [`evaluators/run_suite.py`](evaluators/run_suite.py) | loads the suite into LangSmith, runs `langsmith.evaluate()`, writes `.eval-results/suite.json` and `suite-summary.md` |
| [`results-summary.md`](results-summary.md) | the results table template (metric, target, actual, pass) per category, with the recorded runs |
| [`datasets/`](datasets/), [`baseline.json`](baseline.json) | the repository's four datasets and the gate's baseline (unchanged by this package) |

## Run it

From the repository root, with the stack up (`docker compose ps`: every service healthy, `curl localhost:8010/ready`
→ `"ok":true`) and the model door reachable (`/ready` → `model_door`; with the interim door, `make bridge` in a
second shell and `curl localhost:8099/health`):

```bash
source .venv/Scripts/activate                    # Linux and macOS: source .venv/bin/activate
python -m evals.evaluators.run_suite             # all 29 rows, judges on, experiment suite-<sha>-<hash>
python -m producery.evals.gate                   # the gate now includes .eval-results/suite.json
```

Options: `--rows a,b` (a subset), `--no-judges` (deterministic checks only, about a third of the model calls),
`--local` (no LangSmith experiment, results to the file only), `--prefix name`. A full run with judges takes
about 35 minutes on the interim door and spends about 120 traces.

With the interim door on a host where `host.docker.internal` resolves to a VPN interface, run the host side with
`LANGSMITH_GATEWAY_BASE_URL=http://127.0.0.1:8099/v1` (the containers keep the `.env` value). On a Windows
console set `PYTHONIOENCODING=utf-8`.

## Environment

Read through `producery.settings.Settings` from `.env` (`.env.example` is the contract):

| Variable | Used for |
|---|---|
| `LANGSMITH_API_KEY` | the dataset and the experiment in LangSmith; the judge's credential at the door |
| `LANGSMITH_TRACING` | `true` records an experiment; `false` keeps the key for the sync and writes results to the file only |
| `LANGSMITH_ENDPOINT`, `LANGSMITH_PROJECT` | the workspace and the tracing project |
| `LANGSMITH_GATEWAY_BASE_URL`, `MODEL_ACCESS_MODE` | the model door the agents and the judge call |
| `AGENT_API_TOKEN`, `EVAL_AGENT_URL`, `EVAL_THREAD_CLASS` | the agent the target posts to (`localhost:8010`), under the `eval` thread class so suite memory never reaches `crew:` threads |

## Reproducibility

- Dependencies: `requirements.lock` pins the environment `make setup` installs (the lock first, then the package
  with its dev extras), the same lock the images install.
- Rows are content-hashed; a changed row is a new LangSmith example, never a silent edit. The experiment metadata
  carries the commit, the revision id and the suite hash.
- The judge runs on the `judge` tier of `config/models.yaml` at temperature 0; on the interim door every tier is
  served by the same CLI, so the cross-family property holds only once the Gateway serves.
- Expect variance on the interim door: the analyst's tool-call count moves between runs, which is why trajectory
  step caps carry headroom (`crew-002`: 12).

## Existing artifacts this package builds on

| Artifact | Where |
|---|---|
| evaluation design and recorded results | [`docs/evals.md`](../docs/evals.md) (sections 1 to 3 plan and thresholds, 4 online scoring, 5 the golden-row loop, 6 the recorded run of 2026-10-13) |
| the harness | [`producery/evals/evaluators.py`](../producery/evals/evaluators.py), [`run.py`](../producery/evals/run.py), [`gate.py`](../producery/evals/gate.py), [`online.py`](../producery/evals/online.py), [`targets.py`](../producery/evals/targets.py), [`datasets.py`](../producery/evals/datasets.py) |
| the four repository datasets | [`datasets/classifier.jsonl`](datasets/classifier.jsonl) (7), [`crew_e2e.jsonl`](datasets/crew_e2e.jsonl) (14), [`extractor.jsonl`](datasets/extractor.jsonl) (4), [`tools.jsonl`](datasets/tools.jsonl) (10), [`_gate_red_proof.jsonl`](datasets/_gate_red_proof.jsonl) (the gate's self-test) |
| thresholds and baseline | [`config/eval_gates.yaml`](../config/eval_gates.yaml) (G1 to G9, the smoke subset), [`config/langsmith/online_evals.yaml`](../config/langsmith/online_evals.yaml), [`baseline.json`](baseline.json) |
| the judge prompt | [`prompts/judge_draft.yaml`](../prompts/judge_draft.yaml) (Prompt Hub `producery-judge-draft`, pinned in `config/prompts.yaml`) |
| the recorded gate verdict | [`results/gate_report-2026-10-13.md`](results/gate_report-2026-10-13.md) (a dated copy; the live report is `.eval-results/gate_report.md`, gitignored) |
| the pipeline that runs it | [`pipeline/pipeline.yaml`](../pipeline/pipeline.yaml), [`docs/pipeline.md`](../docs/pipeline.md) |
| how to add a row or a check | [`docs/cookbook/evals.md`](../docs/cookbook/evals.md), [`scripts/mint_golden.py`](../scripts/mint_golden.py) |
| LangSmith, 2026-10-13 run with every row clean | experiments `run-a0ed473-classifier-1f643274`, `run-a0ed473-crew_e2e-2e6aa9ad`, `run-a0ed473-extractor-7d830411`, `run-a0ed473-tools-ae22e251` (LangSmith → Datasets → `producery-<name>` → Experiments) |
