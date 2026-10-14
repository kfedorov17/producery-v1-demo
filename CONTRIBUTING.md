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
- Configuration is read through `producery.settings.Settings`, never from `os.environ`; model identifiers live
  only in `config/models.yaml`; prompts are pushed with `make prompts-push`, which rewrites `config/prompts.yaml`
  (commit both).
- Agents, tools and their grants are config artifacts in `config/agents.yaml`; `tests/test_config_doctrine.py`
  pins the agent set and the grants, so changing them means changing the test in the same commit.
- A behaviour change comes with a unit test, and a change that affects an agent's output with an eval row
  (`docs/cookbook/evals.md`).
- Documentation lives in `docs/`; the Mermaid sources in `docs/diagrams/*.mmd` are the single source of truth for
  the diagrams.

Work on a branch; commit messages name the subsystem first (`docs(cookbook): …`, `fix(worker): …`).
