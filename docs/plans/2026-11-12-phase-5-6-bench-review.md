# Phase 5.1 and 6.1–6.2 — bench runner and blind review

Plan for `docs/PLAN.md` §11. Two pieces that are code and can be proven without a provider key or a
human: the runner that drives a task x agent grid, and the blind review packaging plus the page the
owner will actually use for the 90 verdicts.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| 5.1/5.2 | `src/fixjury/bench.py` + `fixjury bench` — plan from the corpus index, queue the grid, drain the queue one transaction per run, stop on a time budget, optionally verify each finished run | unit tests on the plan; integration tests on enqueue and drain |
| 6.1 | `src/fixjury/review/pack.py` — one batch per task, stored shuffle seed, mail headers and object ids stripped, agent/model/cost/timing never in the payload | 10 unit tests, including "the blind item carries no identity" |
| 6.2 | `src/fixjury/review/app.py` + templates — FastAPI page: task, patch, check results, rubric, accept/reject with taxonomy reasons | 8 integration tests against the real database and the real app |

## Design

### Bench

`load_plan` reads the corpus index unless tasks are named; every selected task is parsed, so a typo
fails before anything is queued. `enqueue_plan` writes one run row per pair — the unique constraint
keeps the grid honest. `drain` claims one run per transaction: a crash must not roll back the runs
that already finished, and a time budget stops the bench instead of letting it overrun. With
`--verify` every finished run is verified as it lands, which is the whole pipeline in one command.

### Blind review

A batch is the runs of **one task** by several agents, shuffled with a per-task seed that is stored
in `review_batches.order_seed`, so the order is never regenerated and blindness is auditable. Only
patches with `automated_pass = true` reach a human (PLAN §3.1), and a task needs at least two of them
to make a batch — a batch of one is not a comparison.

`BlindItem` is a frozen dataclass with four fields: run id, position, patch, check results. There is
no field for an agent, a model, a cost or a timing, so the page cannot leak one by accident. The diff
is normalised: `From`/`Author`/`Date`/`Subject`/`Commit` lines and `index a..b` lines are removed.

The page writes only `human_*` fields, refuses a rejection without a reason, and refuses a second
verdict for the same patch. Form data is parsed by hand rather than adding a form-parser dependency.

## Verification checkpoint

- `uv run pytest -q` green; `uv run mypy src tests` clean.
- `fixjury bench --agents baseline --tasks T-001,T-002 --verify` queues 2 runs, drains them and
  verifies the finished ones.
- Packing twice produces the same batch id, not a duplicate.
- The review page renders the patch, the checks and the rubric, and contains no agent name, no model
  name and no cost.
- An accept is stored with `blind = true`, the batch id and the seconds spent; a second POST for the
  same patch returns 409; a rejection without a reason returns 400.
