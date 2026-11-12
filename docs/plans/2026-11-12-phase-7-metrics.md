# Phase 7.1–7.2 — metrics and report

Plan for `docs/PLAN.md` §11 phase 7, first half. Metrics are computed once, stored as snapshots, and
the report reads snapshots only (invariant 5). The static page (7.3) and finalisation (7.4) are not
here: the page needs a deployment decision and the finalisation needs a benchmark run.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| 7.1 | `src/fixjury/metrics/compute.py` — every §3.2 metric as a pure function over plain rows, Wilson CI | unit tests pin each formula, including the null-denominator cases |
| 7.1 | `src/fixjury/metrics/service.py` — read runs + verdicts, store `metric_snapshots` with `config_hash` and `pricing_version` | snapshot cost total equals the sum of `runs.cost_list_usd` |
| 7.2 | `src/fixjury/report/markdown.py` — report tables from snapshot payloads, basis labelled | renderer tests: percentages, CI, `null`, exclusions |
| — | `fixjury metrics compute|show`, `fixjury report` | one command each, from the CLI |

## Definitions carried in from PLAN §3

- Denominator is **pairs (task, agent)**, never attempts.
- `finished` = status `finished` **and** a non-empty diff; `infra_failed`/`cancelled` are exclusions.
- `accepted` = some attempt of the pair has `human_verdict = accept`.
- `regression_free_accepted` = accepted and `heldout_pass = true` and `policy_pass = true`.
- `tokens_per_accepted_fix` = all tokens of the pair / accepted pairs (provider-independent).
- `cost_per_accepted_fix` = total cost / accepted pairs, **`null` when nothing was accepted**.
- `false_completion_rate` = declared success but not accepted / runs with a declared-success signal.
- `heldout_pass = null` is a data defect: such pairs leave the regression-free rate and are counted.

## Design

`Attempt` is a frozen dataclass holding only what the metrics read; `attempts_from_rows` flattens the
`runs ⟕ verdicts` join into it. `compute_metrics` groups by task and returns a `MetricsReport`;
`as_payload` turns it into the JSONB payload with denominators and the cost basis, because a number
without its denominator is not publishable.

`config_hash_of` digests the distinct run configurations behind a snapshot, so two snapshots can be
told apart even when the numbers look similar. Every new computation appends a row: snapshots are
immutable history, and `latest_snapshots` picks the newest per agent.

## Verification checkpoint

- `uv run pytest -q` green; `uv run mypy src tests` clean.
- `fixjury metrics compute --agent <a>` stores one snapshot; a second call stores another.
- The snapshot's `cost_total` equals the sum of `runs.cost_list_usd` for that agent.
- `fixjury report --out /tmp/...` writes a report whose tables carry the basis label, the pricing
  version, the exclusions and `null` where a denominator was zero.
