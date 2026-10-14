# Suite results: suite-12574c3-8f6a537a

- dataset: https://smith.langchain.com/o/7c7b0874-b71c-4b93-9848-8c43e4c5a238/datasets/a10c4104-2818-4dc7-9ec1-3a2d6d998f97
- experiment: https://smith.langchain.com/o/7c7b0874-b71c-4b93-9848-8c43e4c5a238/datasets/a10c4104-2818-4dc7-9ec1-3a2d6d998f97/compare?selectedSessions=7a52a1e4-b7c3-47d9-9ed0-d0a0ff1ad15d

Rows: 29; clean: 29; evaluator errors: 0.

## happy (10 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `cites` | 100 % | 100% (3/3) | pass |
| `classification` | 100 % | 100% (10/10) | pass |
| `compliance_clean` | 100 % | 100% (6/6) | pass |
| `fields` | 100 % | 100% (1/1) | pass |
| `latency` | 100 % | 100% (1/1) | pass |
| `mentions` | 100 % | 100% (3/3) | pass |
| `not_contains` | 100 % | 100% (5/5) | pass |
| `outcome` | 100 % | 100% (10/10) | pass |
| `path` | 100 % | 100% (5/5) | pass |
| `structure` | 100 % | 100% (7/7) | pass |
| `trajectory` | 100 % | 100% (6/6) | pass |
| p95 latency | <= 180 s | 173 s | pass |

## edge (6 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `cites` | 100 % | 100% (2/2) | pass |
| `classification` | 100 % | 100% (6/6) | pass |
| `compliance_clean` | 100 % | 100% (6/6) | pass |
| `latency` | 100 % | 100% (1/1) | pass |
| `not_contains` | 100 % | 100% (6/6) | pass |
| `outcome` | 100 % | 100% (6/6) | pass |
| `recipient` | 100 % | 100% (6/6) | pass |
| `structure` | 100 % | 100% (6/6) | pass |
| `trajectory` | 100 % | 100% (3/3) | pass |
| p95 latency | <= 180 s | 128 s | pass |

## failure (5 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `classification` | 100 % | 100% (5/5) | pass |
| `compliance_clean` | 100 % | 100% (1/1) | pass |
| `error` | 100 % | 100% (1/1) | pass |
| `latency` | 100 % | 100% (3/3) | pass |
| `not_contains` | 100 % | 100% (1/1) | pass |
| `outcome` | 100 % | 100% (5/5) | pass |
| `recipient` | 100 % | 100% (1/1) | pass |
| `structure` | 100 % | 100% (5/5) | pass |
| `trajectory` | 100 % | 100% (4/4) | pass |
| p95 latency | <= 180 s | 153 s | pass |

## redteam (8 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `cites` | 100 % | 100% (1/1) | pass |
| `classification` | 100 % | 100% (7/7) | pass |
| `compliance_clean` | 100 % | 100% (6/6) | pass |
| `fields` | 100 % | 100% (1/1) | pass |
| `injection` | 100 % | 100% (6/6) | pass |
| `mentions` | 100 % | 100% (1/1) | pass |
| `not_contains` | 100 % | 100% (5/5) | pass |
| `outcome` | 100 % | 100% (7/7) | pass |
| `path` | 100 % | 100% (1/1) | pass |
| `recipient` | 100 % | 100% (2/2) | pass |
| `structure` | 100 % | 100% (6/6) | pass |
| `trajectory` | 100 % | 100% (4/4) | pass |
| p95 latency | <= 180 s | 127 s | pass |

## all (29 rows)

| metric | target | actual | pass |
|---|---|---|---|
| `cites` | 100 % | 100% (6/6) | pass |
| `classification` | 100 % | 100% (28/28) | pass |
| `compliance_clean` | 100 % | 100% (19/19) | pass |
| `error` | 100 % | 100% (1/1) | pass |
| `fields` | 100 % | 100% (2/2) | pass |
| `injection` | 100 % | 100% (6/6) | pass |
| `latency` | 100 % | 100% (5/5) | pass |
| `mentions` | 100 % | 100% (4/4) | pass |
| `not_contains` | 100 % | 100% (17/17) | pass |
| `outcome` | 100 % | 100% (28/28) | pass |
| `path` | 100 % | 100% (6/6) | pass |
| `recipient` | 100 % | 100% (9/9) | pass |
| `structure` | 100 % | 100% (24/24) | pass |
| `trajectory` | 100 % | 100% (17/17) | pass |
| p95 latency | <= 180 s | 153 s | pass |
