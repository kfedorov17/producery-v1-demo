# Contributing

Before a change is pushed it passes the same three commands CI runs first:

```bash
make lint         # ruff check (incl. Google-style docstrings on public code) and ruff format --check
make typecheck    # mypy over producery/
make test         # the unit suite: no network, seconds
```

The whole CI stage, including the compose stack, the Playwright suite and the eval gate, is
`python scripts/pipeline.py ci` from the activated venv; `docs/pipeline.md` explains every step.

Conventions, so a change reads like the rest of the repository:

- Public modules, classes, functions and methods carry a docstring in the Google style (`Args:`, `Returns:`,
  `Raises:`); ruff's `D` rules enforce it, and `tests/` need no docstrings.
- Application services read configuration through `producery.settings.Settings`; only the stdlib-only executor
  daemon, the host-side bridge and the host scripts read the environment directly, and every variable they read is
  documented (`.env.example`, `docs/cookbook/sandbox.md`, `docs/cookbook/first-turn.md`). Model identifiers live
  only in `config/models.yaml`; prompts are pushed with `make prompts-push`, which rewrites `config/prompts.yaml`
  (commit both).
- Agents, tools and their grants are config artifacts in `config/agents.yaml`; `tests/test_config_doctrine.py`
  pins the agent set and the grants, so changing them means changing the test in the same commit.
- A behaviour change comes with a unit test, and a change that affects an agent's output with an eval row
  (`docs/cookbook/evals.md`).
- Documentation lives in `docs/`; the Mermaid sources in `docs/diagrams/*.mmd` are the single source of truth for
  the diagrams, and the fences in `docs/architecture.md` repeat them verbatim. Re-render after a change with
  `npx -y @mermaid-js/mermaid-cli@11.4.2 -i docs/diagrams/<view>.mmd -o docs/diagrams/<view>.png -s 4 -b white`
  (and `-o <view>.svg`); on a host without a bundled Chromium, pass `-p puppeteer.json` naming one.

Work on a branch; commit messages name the subsystem first (`docs(cookbook): …`, `fix(worker): …`).
