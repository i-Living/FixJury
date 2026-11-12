# Phase 4 — queue, cost, traces (adapters deferred)

Plan for `docs/PLAN.md` §11 phase 4, split in two: this change lands the parts that need no
third-party harness (4.3 queue and worker, 4.4 cost and traces); the SWE-agent and OpenHands
adapters (4.1, 4.2) come next, because they need their own images and output formats and a
half-working adapter is worse than none.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| 4.3 | `src/fixjury/queue.py` + `src/fixjury/worker.py` — Redis lists, atomic claim, retry as a new attempt, `resume` after a crash | a crash leaves the run in `processing` and `resume` returns it; a failed attempt queues attempt 2; no duplicate rows |
| 4.3 | `fixjury queue add|status|resume`, `fixjury worker --once` | a queued run is executed and leaves both lists empty |
| 4.4 | `src/fixjury/metrics/cost.py` + `config/pricing.yaml` — list price from tokens and sandbox seconds, with a `pricing_version` | arithmetic unit tests; the shipped file is explicitly `unpriced` |
| 4.4 | `src/fixjury/telemetry.py` — a span per run and per tool step, `runs.trace_id` recorded | `FIXJURY_OTEL_CONSOLE=1` prints the spans; the row carries the same trace id |

## Non-negotiables carried in from `AGENTS.md`

- Duplicates are forbidden by the schema (`uq_runs_task_agent_attempt`), not by the queue.
- A retry is a new attempt row; the failed attempt stays visible.
- Costs carry `pricing_version`; rows with different versions are never compared silently.
- `cost_per_accepted_fix` is `null`, not `0`, when nothing was accepted — that lands with metrics.

## Design

### Queue

`queued` and `processing` are Redis lists. `enqueue` writes the row and pushes its id; a worker
takes an id with `BLMOVE queued processing`, which is atomic, so a killed worker leaves the id in
`processing` instead of losing it. `resume` moves everything back. FIFO: `rpush` in, `blmove` from
the left.

`enqueue` refuses a triple whose run is no longer `queued` — the database already prevents a second
row, and silently re-pushing a finished run would execute it twice.

### Worker

One run per claim, one transaction per run. A preparation error (`RunError`) or a crashed adapter
marks the run `infra_failed` and queues `attempt + 1` while the budget lasts (2 attempts), so an
infrastructure failure never looks like a finished run.

### Cost

`runs.cost_list_usd = tokens_in × input_rate + tokens_out × output_rate + sandbox_seconds × hourly`.
The shipped `config/pricing.yaml` has zero rates and `version: unpriced-2026-11`: FixJury runs on a
flat subscription, so there is no invoice, and a made-up rate would put a fabricated number in a
column that reads as authoritative. The arithmetic is proven by unit tests with a synthetic price
list; real rates go into the file and the version changes with them.

### Traces

One `run` span with task, agent and base commit, and one `step` span per tool call with the step
number and tool name. With no exporter the spans are dropped but the ids are still valid, so
`runs.trace_id` is always populated.

## Verification checkpoint

- `uv run pytest -q` green; `uv run mypy src tests` clean.
- `fixjury queue add` → `fixjury worker --once` executes the run and empties both lists.
- `FIXJURY_OTEL_CONSOLE=1` prints `run` and `step` spans for that run.
- The stored row has `trace_id` set, `pricing_version = unpriced-2026-11`, `cost_list_usd = 0`.
