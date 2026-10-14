# Deploy

> Bring the stack up from a clean checkout, run the pipeline stages, roll back, and map every compose service to
> its Kubernetes / LangSmith Deployment counterpart. For whoever ships or hosts it.

## Compose

```bash
cp .env.example .env                       # then set LANGSMITH_API_KEY
make setup                                 # creates .venv, installs the package, generates AGENT_API_TOKEN and SESSION_SECRET
source .venv/Scripts/activate              # Linux and macOS: source .venv/bin/activate
make prompts-push                          # push the ANCHOR prompts, pin their commits (rewrites config/prompts.yaml: commit it)
docker compose up -d --build               # postgres, redis, seed, mcp-data, executor, mcp-exec, agent, worker, web
python scripts/pipeline.py wait-ready      # ready: agent, mcp-data, mcp-exec, worker, web
```

Ports, dependencies and health checks per service: `../deployment.md` §2.

Images build from `BASE_REGISTRY` (the Docker Hub mirror on ECR Public by default; set a private mirror in `.env`).

## The pipeline, locally or in GitHub Actions

```bash
python scripts/pipeline.py ci     # lint → typecheck → unit → web-typecheck → web-unit → build → stack-up → wait-ready → prompts-check → web-e2e → gate-self-test → offline-evals → eval-gate
python scripts/pipeline.py cd     # deploy <sha> → wait-ready → smoke-eval → promote (or rollback to the previous tag)
python scripts/pipeline.py post   # online scoring → baseline refresh (blocked when a metric breaches)
```

`pipeline/pipeline.yaml` is the one definition; `.github/workflows/pipeline.yml` only calls these stages. On hosted
runners there is no model door, so `offline-evals` runs the model-free `tools` dataset and the gate decides on it;
the model-dependent datasets are gated on a host with a door.

## Rollback

`cd` keeps `pipeline/.deploy/current_tag` and `previous_tag`. A failed smoke run rolls back automatically; by hand:

```bash
python scripts/pipeline.py rollback
cat pipeline/.deploy/current_tag && curl -s localhost:8010/ready | grep -o '"revision_id":"[^"]*"'
```

## Kubernetes / LangSmith Deployment

The mapping, service by service, is `../deployment.md` §5.

Checklist before the first deploy: `langgraph.json` resolves, `.env` values come from the platform's environment
(not a file), the demo producer is rotated, `BASE_REGISTRY` points at your mirror. The resolution check imports the
entry point, which builds the composition root: it pulls the pinned prompts from the Hub and connects to both MCP
servers, so it needs the compose stack up and the host-side URLs (the Postgres pool opens lazily):

```bash
MCP_DATA_URL=http://localhost:8091/mcp MCP_EXEC_URL=http://localhost:8092/mcp \
DATABASE_URL=postgresql://producery:producery@localhost:5444/producery \
python -c "import producery.graphs.entrypoint as e; print(type(e.graph).__name__)"
```

```text
CompiledStateGraph
```

## What to read next

- `../deployment.md` — the full mapping with the LangSmith docs it rests on.
- the infrastructure architecture deck delivered beside this repository (not part of it) — the self-hosted LangSmith platform this deploys onto.
