# FixJury

**An agent-agnostic reliability layer for autonomous code repair.**

> A patch that compiles, passes CI and opens a pull request is not a fix. FixJury measures how often a fix survives independent verification and human acceptance — and what one accepted fix actually costs.

**Status: all code phases implemented; the benchmark run and human acceptance are next.** Toolchain, schema, CI, the corpus tooling and a **32-task frozen corpus**. A run executes end to end (`fixjury run`, or the whole grid with `fixjury bench`), is verified in a fresh container (`fixjury verify`), carries list-price cost and an OTel trace id, and accepted patches are packed into blind batches for review (`fixjury review pack|serve`) before metrics and the report (`fixjury metrics compute`, `fixjury report`, `fixjury dashboard` — the
page is a Next.js static export in `web/`). What is missing is not code: it is two pinned harness images, a provider key, and 96 human verdicts. See [`docs/PLAN.md`](docs/PLAN.md).

---

## The problem

Coding agents already turn GitHub issues into patches: SWE-agent, OpenHands, GitHub Copilot coding agent. What is missing is the layer that answers:

- Is the original defect fixed, or only the symptom?
- Did the patch introduce regressions or break a public API?
- Did the agent touch files it was not supposed to touch?
- Would an independent developer accept this patch?
- How much did one **accepted** fix cost?

Public benchmarks answer the first question with hidden unit tests and stop there. Nobody measures human acceptance, regression-free acceptance and cost per accepted fix on the same task set, across different agents. That is the gap FixJury targets.

FixJury is not another coding agent. It runs existing agents as interchangeable executors and judges their output.

## What it measures

| Metric | Definition |
| --- | --- |
| `human_acceptance_rate` | Share of task–agent pairs whose patch a blind human reviewer accepted. |
| `regression_free_acceptance_rate` | Share of task–agent pairs accepted **and** free of detected regressions and out-of-scope changes. |
| `tokens_per_accepted_fix` | Total tokens across all runs (including retries and rejections) divided by accepted fixes — the primary, tariff-independent metric. |
| `cost_per_accepted_fix` | Same denominator, in money: reported on two bases (reference list price for the tokens, and the amortized share of the flat subscription); every table states its basis. `null` when nothing was accepted. |
| `task_success_rate` | Share of task–agent pairs passing every automated check (tests, lint, types, policy, held-out tests). |
| `median_time_to_repair` | Median wall-clock time of accepted runs. |
| `false_completion_rate` | Share of runs where the agent declared success but the patch failed checks or human review. |

Automated success and human acceptance are reported separately, because passing tests is not the same as solving the task. Cost has two bases — reference list price for the tokens, and the amortized share of the flat subscription — and every table states which one it shows. Full formulas, denominators and exclusion rules: [`docs/PLAN.md` §3](docs/PLAN.md).

## How it works

```
GitHub issue / repair task
        |
        v
  RepairSpec  (contract: acceptance criteria, allowed paths, required checks)
        |
        +--------- SWE-agent ---+
        +--------- OpenHands ---+   same task, same limits, same model budget
        +--------- baseline ----+
                                |
                                |  output: unified diff only
                                v
  Verification   fresh container from base_commit + diff:
                 pytest | ruff | mypy | held-out tests | scope policy
                                |
                                v
  Blind human review   reviewer sees the task, the spec and the patch,
                       never the agent identity
                                |
                                v
  Metrics + benchmark report
  acceptance | regressions | time | cost per accepted fix
```

## RepairSpec

Every task reaches an agent as a checkable contract, not as free-form text:

```yaml
id: T-001
repo: https://github.com/pallets/click
license: BSD-3-Clause
base_commit: 8e6b0f0a1c9d4e2f3a5b7c8d9e0f1a2b3c4d5e6f
fix_commit: 3f1c2b4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b
issue:
  number: 2712
  url: https://github.com/pallets/click/issues/2712
  created_at: 2025-09-14
  fix_merged_at: 2025-10-02
title: "Option name with a dash breaks shell completion"
acceptance:
  - "completion output for --foo-bar matches the option name"
  - "no changes outside src/click/shell_completion.py and tests/"
checks:
  - pytest tests/test_shell_completion.py
  - ruff check src tests
  - mypy src
forbidden:
  - "changes to public signatures in src/click/core.py"
  - "edits to files under .github/"
allowed_paths:
  - src/click/shell_completion.py
  - tests/
difficulty: medium
heldout_ref: benchmarks/heldout/T-001
cutoff_assumption: clean
```

The spec is generated from the issue, then **approved by a human at freeze time**. From that moment `task.yaml` is immutable: editing a task after runs started invalidates the comparison.

## Design principles

1. **The patch is the only transport.** Verification never runs in the agent's container; the diff is applied to a pristine checkout of `base_commit`.
2. **Held-out tests never enter the agent's container.** They live in this repository, but the agent only ever receives a checkout of the *target* repository at `base_commit`, with no network; held-out tests are mounted read-only into the verification container only.
3. **Sandbox has no network by default.** For agents that need provider egress, a proxy allowlists exactly one LLM endpoint.
4. **Human acceptance is blind and rule-based.** A fixed rubric, a fixed rejection taxonomy, shuffled review order, and a 10% repeat pass ≥3 days later to measure intra-rater agreement.
5. **Metrics have one implementation.** The report and the dashboard read the same `metric_snapshots` rows; no number is computed in the UI or by hand.
6. **No auto-merge, ever.** FixJury is a measurement and acceptance layer; a human decides what lands.

## MVP scope

| Parameter | MVP decision |
| --- | --- |
| Language of target repos | Python |
| Task source | GitHub issues with a fixed base commit |
| Tasks | 30 (frozen corpus, 50–80 changed lines per fix) |
| Agents | SWE-agent, OpenHands, own minimal baseline |
| Checks | pytest, ruff, mypy, scope policy, held-out tests |
| Acceptance | Human only, blind, fixed rubric |
| Benchmark size | 96 task–agent pairs (32 tasks × 3 agents), up to 2 attempts per pair |
| Report | Public benchmark + engineering report |

**Non-goals for the MVP:** another coding agent, a code review bot, a CI replacement, auto-merge, multi-language support, multi-tenant SaaS, a self-improving prompt loop.

## Repository layout

```
src/fixjury/     platform: sandbox, adapters, verification, review, metrics, API, CLI
benchmarks/      frozen task corpus (task.yaml per task) + index
tests/           unit and integration tests
web/             read-only metrics page: Next.js static export, charts via Chart.js
docs/PLAN.md     specification and phased implementation plan
idea.md          original concept note (Russian)
AGENTS.md        engineering rules for agents working in this repo
```

## Roadmap

| Phase | Week | Outcome |
| --- | --- | --- |
| 0 | 1 | Toolchain, DB schema, CI, docker-compose |
| 1 | 2 | 32 frozen tasks + RepairSpec generator |
| 2 | 3 | Sandbox, diff capture, baseline agent, `fixjury run` |
| 3 | 4 | Verification, held-out suite, policy checks, `fixjury verify` |
| 4 | 5 | Redis queue, worker, cost, traces — adapters next |
| 5 | 6 | Bench runner (`fixjury bench`) — the 96-run pass needs harness images and a key |
| 6 | 7 | Blind review page (`fixjury review`) — the verdicts are the owner's |
| 7 | 8 | Metrics + report (done), static metrics page on the VPS (next) |

Estimates are planning targets for evenings, not commitments.

## Documentation

- [`docs/PLAN.md`](docs/PLAN.md) — specification, data model, metric definitions, phased plan (Russian).
- [`idea.md`](idea.md) — original concept note (Russian).
- [`AGENTS.md`](AGENTS.md) — invariants, conventions and pitfalls for anyone (human or agent) changing this code.

## License

MIT — see [`LICENSE`](LICENSE).
