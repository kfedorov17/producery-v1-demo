# Producery — CI/CD pipeline

In GitHub Actions the `cd` and `post` stages run against a compose stack on the ephemeral runner: a rehearsal of the
deploy → smoke → promote / rollback mechanics, not a deployment to a persistent host (the promote/rollback tag file
lives on the runner). The Kubernetes target is `langgraph.json` under LangSmith Deployment (`deployment.md` §5).

One definition: `pipeline/pipeline.yaml`. One runner: `scripts/pipeline.py <stage>` (identical locally and in
CI). `.github/workflows/pipeline.yml` only calls the stages. Every gate and threshold is repo config
(`config/eval_gates.yaml`, `config/langsmith/online_evals.yaml`); nothing is defined only in a CI vendor's UI.
There is no skip flag for any gate.

## 1. Stage diagram

```
 CI (per push and pull request; `python scripts/pipeline.py ci`)
   lint → typecheck → unit → web-typecheck → web-unit → build (IMAGE_TAG=sha) → stack-up → wait-ready
   → prompts-check (local ANCHOR prompts == pinned Hub commits) → web-e2e (Playwright against the stack)
   → gate-self-test (the committed proof rows must turn the gate RED)
   → offline-evals (every dataset → experiments ci-<sha>-<dataset>)
   → ■ EVAL GATE  G1 must-pass · G2 ≥ 0.95 · G3 judge · G4 regression vs baseline · G5 red-team
                  G6 trajectory · G7 p95 latency · G8 evaluator errors · G9 cost
                                        │ green only (main)
 CD (`pipeline.py cd`)                  ▼
   deploy (compose up -d --wait, IMAGE_TAG=sha; previous tag recorded) → wait-ready
   → ■ SMOKE EVAL (six golden rows through the real stack: crew-004, cls-001, tool-sql-valid/invalid,
       tool-code-valid/timeout; 100 % or on_fail: rollback to pipeline/.deploy/previous_tag)
   → promote (pipeline/.deploy/current_tag = sha)
                                        │
 post (`pipeline.py post`)              ▼
   online scoring over the deployed instance's traces → LangSmith feedback + annotation queue
   → ■ alert thresholds (exit 2 on breach) → baseline refresh (GREEN only)
```

## 2. Stages and steps

| Stage | Step | Command | Gate |
|---|---|---|---|
| ci | lint | `ruff check` + `ruff format --check` | fail = block |
| ci | typecheck | `mypy` | fail = block |
| ci | unit | `pytest` (no network, seconds) | fail = block |
| ci | web-typecheck, web-unit | `npm --prefix web run typecheck`, `npm --prefix web test` | fail = block |
| ci | build | `docker compose build` with `IMAGE_TAG=REVISION_ID=<git sha>` | fail = block |
| ci | stack-up / wait-ready | `docker compose up -d --wait`; `/ready`, `/health` on agent, mcp-data, mcp-exec, worker, web | fail = block |
| ci | prompts-check | `scripts/prompts_push.py --check` (a local prompt edit without a new pinned commit blocks) | fail = block |
| ci | web-e2e | Playwright against the stack (11 specs; the two lifecycle specs skip on hosted runners without a door) | fail = block |
| ci | gate-self-test | `pipeline.py prove-gate`: the committed proof rows must turn the gate RED | fail = block |
| ci | offline-evals | `python -m producery.evals.run --all --prefix ci-<sha>` → experiments (recorded locally when tracing is off); without a model door, the `tools` dataset only | — |
| ci | **eval-gate** | `python -m producery.evals.gate` | **G1–G9; exit code** |
| cd | deploy | `pipeline.py deploy --tag <sha>` (records previous tag) | fail = block |
| cd | **smoke-eval** | `python -m producery.evals.run --suite smoke` against the deployed stack | **100 % or rollback** |
| cd | promote | `pipeline.py promote --tag <sha>` | — |
| post | **online-scoring** | `python -m producery.evals.online` → feedback, annotation queue | **alert thresholds; exit 2** |
| post | baseline | `python -m producery.evals.gate --write-baseline` (refuses on RED) | — |

Rollback: `pipeline.py rollback` redeploys `pipeline/.deploy/previous_tag` with `docker compose up -d --wait`.
Under Kubernetes the same step is the previous Deployment / LangSmith Deployment revision.

How online results feed the next baseline: the post stage writes `evals/baseline.json` from the green offline
results of the deployed revision and records the online summary in `.eval-results/online.json`; the next CI run's
G4 compares its per-check pass rates against that baseline, and minted golden rows (from online failures) join
`crew_e2e` so the gate remembers each confirmed failure.

## 3. Running the stages

A host without a model door (`/ready` → `model_door_configured: false`; GitHub-hosted runners, where neither the
Gateway nor the bridge is reachable) runs the model-free `tools` dataset in `offline-evals` and gates it; the step
prints that it did so. The model-dependent datasets are gated on a developer host with the bridge as the door,
which is how image tags get promoted; the recorded results are in `evals.md` section 6. Hosted runners trace nothing
(`LANGSMITH_TRACING=false` in the runner's `.env`); on `main` the `cd` and `post` jobs then run against the runner's
own stack, a rehearsal of the mechanics rather than a deployment. There is no flag that
skips the gate.

```bash
make bridge                                   # host, keep running
python scripts/pipeline.py ci && python scripts/pipeline.py cd && python scripts/pipeline.py post
```

On a host where `host.docker.internal` resolves to a VPN interface, run the host-side stages with
`LANGSMITH_GATEWAY_BASE_URL=http://127.0.0.1:8099/v1` (containers keep the `.env` value).
