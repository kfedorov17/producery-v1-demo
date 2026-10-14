# Change a prompt

> Change what an agent is told. Prompts are pulled from the LangSmith Prompt Hub by commit (`name:commit`), never
> `latest`. Changing one is: edit the file (or iterate in the Playground), push a commit, let the pin move, prove
> it with the evals, deploy. For whoever owns a prompt.

## Where a prompt lives

| Piece | Path |
|---|---|
| source, ANCHOR structure | `prompts/drafter.yaml` (Assignment, Non-negotiables, Context, How, Output, References) |
| the pin the agent runs | `config/prompts.yaml` → `pins.drafter.commit` |
| what runs right now | `curl -s localhost:8010/agents -H "authorization: Bearer $TOK"` → `prompt: producery-drafter:<commit>` |

## 1. Edit

Keep the section order: the first sections are the stable prefix (byte-identical across turns, cacheable); only
*How* and *Output* usually change. A reorder fails `tests/test_config_doctrine.py`.

```bash
sed -n '1,30p' prompts/drafter.yaml
```

Or iterate in the Playground: LangSmith → Prompts → `producery-drafter` → *Open in Playground*, edit, run against a
saved example — then paste the result back into `prompts/drafter.yaml`. The file is the source of truth; the Hub
commit is made from it.

## 2. See the drift, push, see it clear

```bash
python scripts/prompts_push.py --check      # exit 1: "! drafter: local prompt differs from pin"
python scripts/prompts_push.py              # "+ drafter: pushed → producery-drafter:<new commit>"; config/prompts.yaml rewritten
python scripts/prompts_push.py --check      # exit 0
git diff --stat                             # only prompts/drafter.yaml and config/prompts.yaml
```

## 3. Prove it

```bash
python -m producery.evals.run crew_e2e --rows crew-001,crew-002 --local   # the drafting rows, file recorder only
python -m producery.evals.gate
```

For the full suite and the LangSmith experiments: `make evals` (needs the model door, ~15 minutes).

## 4. Deploy and verify the pin

The agent pulls pins at startup, so the new commit runs after a restart of the image that carries the new
`config/prompts.yaml`.

```bash
docker compose build agent && docker compose up -d agent
curl -s localhost:8010/agents -H "authorization: Bearer $TOK" | grep -o '"prompt": *"producery-drafter:[0-9a-f]*"'
```

The next trace's metadata `prompts.drafter` shows the new commit; older traces keep the old one — that is how you
tell which prompt served which answer.

## Revert

`git checkout prompts/drafter.yaml config/prompts.yaml` restores the previous pin; the Hub keeps every commit.

## What to read next

- [Run and extend the evals](evals.md) — turning the trace that motivated the change into a golden row.
- `../pipeline.md` — the CI stage runs `prompts_push.py --check`, so a drifted pin cannot ship.
