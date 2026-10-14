# Producery — Deployment

## 1. The stack and its scaling envelope

The compose stack is drawn in `architecture.md` view 4 (`diagrams/view4-deployment.{mmd,svg,png}`): nine services
on the default network, the executor alone on `exec_net`, LangSmith beside the flow. Images come from one
Dockerfile with build targets: `agent`, `worker`, `mcp-data`, `seed` (embedding model baked in at build time, so
no runtime download), `mcp-exec` (no embedding model), `executor` (`python:3.11-slim` plus one stdlib file), and
`web/Dockerfile` for the producer UI.

Every service is disposable and horizontally scalable: no local filesystem state (the only volume is Postgres
data), no in-process singletons that matter across replicas (idempotent proposal staging keyed by thread id,
`FOR UPDATE SKIP LOCKED` inbox claims, arq job ids, `run_claims` rows so two replicas never run one thread). The
numbers that bound one replica today, all from the repository, and the signal that says when to add another:

| Service | One replica today | Bounded by | Scale when | Add |
|---|---|---|---|---|
| `agent` | one uvicorn process; one `AsyncConnectionPool` of 1 to 8 connections; a drafting turn holds one connection for about 90 to 150 s on the interim door (p95 118 s over the 14 crew rows), a deterministic route about 1 s and no model call (`evals.md` §6) | the model door's latency and `MODEL_TIMEOUT_S=90`; `TURN_TIMEOUT_S=240` per turn | more than about 5 drafting turns in flight (the pool) or `/ready` p95 over 1 s | replicas behind the service; the pool is per process, Postgres `max_connections` is the shared ceiling |
| `worker` | `max_jobs = 4` concurrent jobs; `poll_inbox` every 15 s claims every `new` row; `run_crew` waits up to `TURN_TIMEOUT_S + 60` s per email; three tries with 15 s / 30 s backoff, then dead-letter | 4 turns in flight per replica, so about 4 drafting emails per 90 s at the door's speed | inbox rows stay `new` for more than one poll interval | replicas (claims are `SKIP LOCKED`, job ids idempotent) |
| `mcp-data` | stateless FastMCP; one read-only transaction per call with a 15 s statement timeout; rows capped at 200, hits at 10 | Postgres read capacity on the four views and the chunk index | tool latency rows in the eval gate (`G7`, p95 180 s per turn) drift up | replicas behind the service; a read replica of Postgres |
| `mcp-exec` + `executor` | one `run_code` call is one fresh process: 512 MB, 1 CPU, 64 pids per container, `RLIMIT_AS` 256 MB per snippet, `timeout_s` at most `EXEC_MAX_TIMEOUT_S=60` | the container's 1 CPU: snippets queue behind each other | more than one sandboxed computation at a time | executor replicas, or `CODE_EXECUTOR=langsmith` (Sandboxes scale per call) |
| `web` | Next.js standalone; proxies to the agent; 10 failed sign-ins per name per minute | the agent's limits | never first | replicas behind the ingress (the session is a signed cookie) |
| `postgres`, `redis` | one container each; checkpoints 30/2/1 days by thread class, episodic memory 180 days, so the data set stays bounded | disk and connections | the pools above saturate | managed Postgres with pgvector and managed Redis (§4, §5) |

Load assumption behind the envelope: a small agency's inbox, tens of emails an hour at peak; a single replica of
each service covers it with room, and the first service to need a second replica is the worker, because its four
job slots are held for the full length of a drafting turn. Under LangSmith Deployment the Agent Server's queue
workers are KEDA-scaled on queue depth and the same signals apply (§5).

## 2. Compose topology

| Service | Image | Port (container → host) | Depends on | Health |
|---|---|---|---|---|
| postgres | pgvector/pgvector:pg16 | 5432 → 5444 | — | `pg_isready` |
| redis | `${BASE_REGISTRY}/redis:7-alpine` — `BASE_REGISTRY` defaults to the Docker Hub mirror on ECR Public (no anonymous pull limit); python and node base images use the same setting; set it to a private pull-through mirror in production | — | — | `redis-cli ping` |
| seed | producery/seed | — | postgres, redis healthy | exits 0 |
| mcp-data | producery/mcp-data | 8081 → 8091 | seed completed | `GET /health` |
| executor | producery/executor | none (exec_net only) | — | via mcp-exec |
| mcp-exec | producery/mcp-exec | 8082 → 8092 | executor | `GET /health` (probes the executor) |
| agent | producery/agent | 8000 → 8010 | seed, mcp-data, mcp-exec healthy | `GET /health` (liveness), `GET /ready` (Postgres, pinned prompts, tools, model door) |
| worker | producery/worker | 8090 → 8090 | agent healthy | `GET /health` |
| web | producery/web (`web/Dockerfile`, Node 22, Next.js standalone, non-root) | 3000 → 3010 | agent healthy | `GET /api/health` |

Host ports are offset from the container ports; compose owns the mapping, `.env` only carries in-mesh URLs.

Networks: `default` (all services except the executor) and `exec_net` (`internal: true`: only `mcp-exec` and
`executor`; no route to the internet or to the rest of the stack).

## 3. Configuration and secrets

All configuration is environment (`pydantic-settings`, `producery/settings.py`); `.env.example` is the
contract. Secrets: `LANGSMITH_API_KEY`, `AGENT_API_TOKEN` (bearer token every caller of the agent API presents;
the app refuses to start without it) and, only if the interim door is chosen, `BRIDGE_TOKEN` on the host. Every
service lists the variables it reads in `docker-compose.yml`: the executor receives exactly two
(`EXEC_MAX_TIMEOUT_S`, `EXEC_WORK_ROOT`), `mcp-exec` the executor URL and settings, `mcp-data` the database and
embedding settings, `seed` the database and queue URLs, `web` the agent token, the session secret and the LangSmith
ids for trace links, `agent` and `worker` the application variables (not the web session secret, not the host-side
harness URLs), and postgres only its own three. Under Kubernetes the Secret is mounted per service (§5).
`REVISION_ID` is a build argument baked into every image (`REVISION_ID=$(git rev-parse --short HEAD) docker compose
build`) and must not appear in `.env`, so every trace stamps the build that served it. Postgres roles: `producery` (app),
`producery_readonly` (`sql_query` runs under `SET LOCAL ROLE`; the role has SELECT on the four views and, for `documents_search`, on `documents` and `document_chunks`; the SQL allowlist, not the grant, keeps `sql_query` on the views). Prompts come
from the Hub by commit; model ids live only in `config/models.yaml`.

## 4. External vs in-container dependencies

| Dependency | Compose | Production |
|---|---|---|
| PostgreSQL + pgvector | container | managed (RDS / Cloud SQL / Azure Database) with pgvector |
| Redis | container | managed Redis/Valkey (ElastiCache / Memorystore) |
| LangSmith | SaaS | SaaS or self-hosted (`LANGSMITH_ENDPOINT`) |
| Model providers | behind the LangSmith LLM Gateway (design) | same |
| Embedding model | baked into images | same (or a shared embedding service behind the same `Embedder` protocol) |

## 5. Kubernetes / LangSmith Deployment migration (one page)

Retention parity: `langgraph.json` expresses one `checkpointer.ttl` (30 days) for all thread classes, while compose prunes
per class (30/2/1 days). If per-class retention matters under Deployment, schedule the worker's `prune_checkpoints` against
the Deployment database. The store index is the same function in both runtimes (`producery/memory/embed.py`, 384-d).

Target: a self-hosted LangSmith platform on Kubernetes (documented separately). Nothing in the application
assumes compose: service discovery is URL env vars, state lives in Postgres/Redis, health endpoints exist,
logs are JSON on stdout, images are per-service.

| Compose service | LangSmith-architecture counterpart | What changes |
|---|---|---|
| `agent` (FastAPI + graph) | **Agent Server under LangSmith Deployment** (self-hosted with control plane, or Hybrid). `langgraph.json` points at `producery/graphs/entrypoint.py:graph` (a compiled graph built once at import — the shape the Agent Server loads at startup; a callable would be a per-request factory and block the event loop on the Hub pulls); the platform injects the checkpointer and store, provides assistants/threads/runs, `/stream`, cron jobs, KEDA-scaled queue workers, checkpoint TTL (`checkpointer.ttl`) and the store index [LS-AGENTSRV, LS-APPSTRUCT] | FastAPI endpoints retire in favour of the Agent Server API (`POST /threads/{id}/runs` with `Command(resume=…)` for HITL); the graph code is unchanged. Decision: Agent Server (not standalone) so the control plane handles revisions, scaling and the UI; standalone needs the same license and hand-built autoscaling |
| model traffic (`producery/model/door.py`) | **`agent-gateway`** (LLM Gateway) at `https://<host>/gateway/v1`; provider secrets, fallback chains, spend/rate/data policies configured in LangSmith | `LANGSMITH_GATEWAY_BASE_URL` changes; `config/models.yaml` chains are re-entered as Gateway fallback chains; `MODEL_ACCESS_MODE=gateway` (already the default) |
| `mcp-exec` + `executor` | **LangSmith Sandboxes** on a KVM node pool with JuiceFS; `CODE_EXECUTOR=langsmith` selects `LangSmithSandboxExecutor` (same `run_code` contract); the `executor` container is deleted | zero agent-facing change |
| manual failure→golden-row loop (`scripts/mint_golden.py`, annotation queue) | **Engine** (needs Sandboxes and the entitlement): scheduled scans, clustering, fix PRs, minted examples | connect the GitHub App and enable Engine on the project; the mint script stays as the manual fallback |
| `mcp-data` | a Deployment behind a ClusterIP Service (MCP over streamable HTTP, stateless) | `MCP_DATA_URL` |
| `worker` | a Deployment (arq) or Agent Server cron jobs for `poll_inbox`/`prune` | `REDIS_URL`, `AGENT_URL` |
| `postgres` | external PostgreSQL (RDS/Cloud SQL) with pgvector — the same managed tier the platform uses, separate database | `DATABASE_URL`; Agent Server needs its own database per deployment |
| `redis` | external Redis/Valkey — separate database number per consumer | `REDIS_URL` |
| traces | ClickHouse (platform default) or **SmithDB** for deep agent traces, Trajectory view and trajectory evaluators | none in app code |
| large artifacts (none today: state holds ids only, documents live in Postgres) | blob storage (S3/GCS/Azure) via the platform when sandbox artifacts or attachments appear | optional |
| `.env` | ConfigMap + Secret with the same names | none in code |
| health checks | liveness `/health`, readiness `/ready` → probes | none |

Rollback is the same mechanism at both layers, redeploying the previous image tag or Deployment revision (`pipeline.md`
section 2); `IMAGE_TAG` has no default in compose, so a bare `docker compose up -d <service>` keeps the promoted images.
