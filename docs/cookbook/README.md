# Cookbook

> Task-first pages. Each answers one "how do I…" with commands to paste against the compose stack; the two "add"
> pages are templates to adapt.

Prerequisite for all pages: the stack is up (`docker compose up -d --build`, `curl localhost:8010/ready` →
`"ok":true`), the venv `make setup` created is activated, and your shell has the token:

```bash
source .venv/Scripts/activate        # Git Bash on Windows; on Linux and macOS: source .venv/bin/activate
TOK=$(grep -E '^AGENT_API_TOKEN=' .env | cut -d= -f2-)
```

| Page | Read this when… |
|---|---|
| [Run your first agent turn](first-turn.md) | you want to see an email go in, a cited draft come out, and find its trace |
| [Add a new MCP tool](add-a-tool.md) | the analyst needs a capability it does not have |
| [Add or modify a subagent](add-a-subagent.md) | you are tempted to give the drafter a tool, or to add a specialist |
| [Change a prompt](change-a-prompt.md) | a draft keeps doing the same wrong thing |
| [Work with memory](memory.md) | you want to know what the crew remembers, and what it refuses to remember |
| [Execute code safely](sandbox.md) | you need arithmetic or a computed figure, and want to see the limits |
| [Run and extend the evals](evals.md) | a trace looked wrong and you want it to stay fixed, or you want to run the test suites |
| [Operate it](operate.md) | something paused, failed, or you need to read a trace like an operator |
| [Deploy](deploy.md) | you want the compose stack on Kubernetes or LangSmith Deployment |
| [Set up a LangSmith workspace](langsmith-workspace.md) | you are starting from a new LangSmith account, or the free tier's monthly trace quota ran out |

## Shell and Windows

Every block is bash: `VAR=$(…)`, `until … do … done`, single-quoted JSON, `export`. On Windows run them in Git
Bash, where they work as written, with these four traps:

- **`python` means the venv's interpreter.** `make setup` creates `.venv`; activate it (above) or prefix commands
  with `.venv/Scripts/python`. The Windows Store `python` alias can hijack a bare `python -m venv`; if `make setup`
  opens the Store, run `py -3.11 -m venv .venv` first. The Makefile already uses `.venv/Scripts/python.exe` on
  Windows.
- **`make`** is not installed with Git for Windows. Install GNU make into Git Bash, or run the lines under each
  target in the `Makefile`; CI parses the Makefile with `make -n` on every push, so the recipes are current.
- **Paths that start with `/`** are rewritten to Windows paths by Git Bash before they reach Docker. Prefix such a
  command with `MSYS_NO_PATHCONV=1` (the `lock` target and the sandbox page do this).
- **PowerShell** aliases `curl` to `Invoke-WebRequest` and has no `export`; use Git Bash for the blocks, or
  `curl.exe` and `$env:NAME = "value"` if you translate them.

Two more: when host-side evals or the judge time out while the stack is healthy, `host.docker.internal` is
resolving to a VPN interface; run the host-side command with `LANGSMITH_GATEWAY_BASE_URL=http://127.0.0.1:8099/v1`
(containers keep the `.env` value). And `pip install` of a package with a very long module path can fail on
Windows until long paths are enabled (`git config --system core.longpaths true` and the Windows policy).

The design behind these pages: [`../design.md`](../design.md); the pictures: [`../architecture.md`](../architecture.md).
