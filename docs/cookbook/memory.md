# Work with memory

> Read what the crew remembers, watch a fact come back in another thread, and see what the write policies refuse.
> Three kinds of long-term memory on the LangGraph store, each with one write path, all written by the `remember`
> node. For an engineer or operator asking what the system learns.

## What is stored

```bash
docker compose exec -T postgres psql -U producery -d producery -c \
  "select prefix, count(*) from store group by prefix order by 1;"
```

Namespaces are `memory.<kind>.<agency>.<thread class>`. Evaluation threads (`eval`, `smoke`) never share a
namespace with production (`crew`).

| Kind | Written by | From | Never from | TTL |
|---|---|---|---|---|
| semantic (`fact-…`) | `remember` | an **executed** `update_record` (the extractor's per-field confidence, the proposal id) and an **approved or edited-and-sent** client reply (the owner-brief sentences that carry a kept citation, ≤ 5, confidence 1.0) | a draft, a model opinion, an unexecuted or rejected proposal, an uncited sentence | none |
| procedural (`pref-…`) | `remember`, from the decision `apply_decision` recorded | a human **edit** or a **reasoned** rejection | the model's own output | none |
| episodic (`ep-crew:<id>`) | `remember` | every terminal outcome; sender withheld for personal mail | — | 180 days |

```bash
docker compose exec -T postgres psql -U producery -d producery -c \
  "select key, value->>'origin' as origin, left(value->>'text', 70) as text from store where prefix='memory.procedural.lakeview.crew' order by updated_at desc limit 5;"
```

`origin` is always `human_edit` or `human_reject`: `producery/memory/store.py` asserts it.

## Write a preference, see it recalled

Reject a draft with a reason, then send the same kind of email again.

```bash
curl -s -X POST localhost:8010/runs -H "authorization: Bearer $TOK" -H 'content-type: application/json' \
  -d '{"wait":false,"email":{"id":"cb-mem-0","from_email":"statements@keystonemutual.example","from_name":"Keystone Mutual Commissions","subject":"September statement","body":"Your September commission statement is attached."}}'
until curl -s localhost:8010/threads/crew:cb-mem-0 -H "authorization: Bearer $TOK" | grep -qE '"status":"(paused|done|failed)"'; do sleep 5; done
python scripts/approve.py reject crew:cb-mem-0 --reason "bookkeeper files these"
```

(`python scripts/demo.py run <id>` does the same for a fixture email already in the inbox; a fixture whose proposal
executed before answers `proposal_already_executed`, which is why this page uses a fresh id.)

```bash
curl -s -X POST localhost:8010/runs -H "authorization: Bearer $TOK" -H 'content-type: application/json' \
  -d '{"email":{"id":"cb-mem-1","from_email":"statements@keystonemutual.example","from_name":"Keystone Mutual Commissions","subject":"October statement","body":"Your October commission statement is attached."}}' \
  | python -c "import sys,json; d=json.load(sys.stdin); print([m['text'] for m in d['values']['memories']['procedural']])"
```

The procedural list contains "Owner rejects billing_commission drafts when: bookkeeper files these" — recalled by
semantic search over the new email, and placed in the brief for the producer.

## What the policies block

- A draft the producer **approved unchanged** writes no preference (nothing to learn).
- An `update_record` the producer **rejected** writes no facts (`execute` never ran).
- Personal mail writes an episode with `sender: null` and `text: (withheld)…` — check with
  `select value->>'sender', value->>'text' from store where key='ep-crew:personal-dinner';`.
- A citation the drafter copied from recalled memory is dropped before staging: memory is context, never a source
  (`producery/guards/citations.py`).

## Retention

```bash
cat config/retention.yaml
docker compose logs agent | grep "TTL sweeper"
```

Episodic rows carry `expires_at`; the store's sweeper removes them. Checkpoints (not memory) are pruned per thread
class by the worker's `prune_checkpoints` job.

## What to read next

- [Run your first agent turn](first-turn.md) — the turn whose outcome becomes an episode.
- `../architecture.md` view 3 — the state and memory picture.
