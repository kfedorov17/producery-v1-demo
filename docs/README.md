# Documentation index

What each page covers and who it is for. Read `architecture.md` first; the rest in any order.

| Page | Covers | For |
|---|---|---|
| [architecture.md](architecture.md) | the system in four diagrams (system, one request, state and memory, deployment) and a components table; the Mermaid sources in `diagrams/` are the single source of truth | anyone who needs the picture in five minutes |
| [design.md](design.md) | the reasoning: graph and nodes, agents and the subagent, tools, model door, prompts, memory tiers, compliance gates, security and failure boundaries | engineers reviewing the design |
| [evals.md](evals.md) | the evaluation strategy: checks, datasets, gates, online scoring, the failure to golden-row loop, and the recorded results | anyone judging whether the agents work |
| [pipeline.md](pipeline.md) | the one pipeline definition: CI, CD and post stages, the gate that can go RED, rollback, and the recorded runs | whoever ships a change |
| [deployment.md](deployment.md) | the compose stack today, its scaling envelope, and the Kubernetes / LangSmith Deployment path | platform and operations |
| [friction-log.md](friction-log.md) | thirty-four things that were awkward in LangChain, LangGraph, Deep Agents and LangSmith, with a wish for the product team on most | the LangChain product team |
| [../evals/README.md](../evals/README.md) | the evaluation package: the plan, the 29-row suite (happy, edge, failure, red-team), the deterministic and judge evaluators, the runner and the results template | anyone judging the evaluation design, or extending it |
| [cookbook/](cookbook/README.md) | task-first how-to pages: first turn, add a tool or a subagent, change a prompt, memory, sandbox, evals, operate, deploy, LangSmith workspace | anyone changing or operating the system |
| [diagrams/](diagrams/) | `view1-system`, `view2-request`, `view3-state-memory`, `view4-deployment` as `.mmd`, `.svg` and `.png` | slides and docs |

## Repository map

| Path | Contents |
|---|---|
| `producery/graphs` | the `inbound_crew` graph: typed state with reducers, nodes, graph construction, the Deployment entry point |
| `producery/agents` | agent definitions loader (`config/agents.yaml`) and the factory that builds the deep agent and its subagent |
| `producery/tools` | the tool registry: MCP tool names → LangChain tools, granted per agent |
| `producery/mcp_data`, `producery/mcp_exec`, `producery/executor` | the two MCP tool services and the sealed code executor |
| `producery/model` | the model access door (Gateway, or the flagged interim fallback) |
| `producery/prompts` | pinned Prompt Hub pulls |
| `producery/memory`, `producery/store` | three-tier memory on the LangGraph store; application tables, records, schema |
| `producery/guards` | compliance gate, citation guard, heuristics |
| `producery/server` | FastAPI: `/runs`, `/threads/{id}/*`, probes, the UI integration layer |
| `producery/worker` | arq jobs: poll the inbox, run the crew, ingest documents, prune checkpoints |
| `producery/evals` | datasets sync, evaluators, run, gate, online scoring |
| `web/` | the producer UI (Next.js), its Dockerfile, unit and Playwright tests |
| `config/`, `prompts/` | every setting that is not an environment variable; the ANCHOR prompt sources |
| `evals/datasets`, `fixtures/` | eval rows (JSONL) and the seed data (book, documents, emails) |
| `tests/` | the unit suite (graph nodes and routing, guards, tools, evals, repo contract) |
| `scripts/` | host tools: demo, approve, prompts push, mint golden rows, seed, MCP smoke, the pipeline runner |
| `pipeline/`, `.github/workflows` | the one pipeline definition and the workflow that calls its stages |
| `bridge/` | the interim model door (option 3), host-side |
| `docs/` | this documentation |
| `Dockerfile`, `web/Dockerfile`, `docker-compose.yml`, `langgraph.json` | images, the stack, the LangSmith Deployment packaging |

Runtime artifacts the pages refer to (`.eval-results/`, `pipeline/.deploy/current_tag`) are produced locally by the
eval runner, the gate and the pipeline and are gitignored; a recorded gate verdict is a dated copy under
`evals/results/`.
