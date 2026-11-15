# FixJury V1.1 — Target State

**Status:** approved 15 November 2026 — [`SPEC_V1.1.md`](../SPEC_V1.1.md) + [`PLAN_V1.1.md`](../PLAN_V1.1.md).
**Baseline:** [`current-state.md`](current-state.md) at `c5fc958`.
**Purpose:** what V1.1 adds, what it deliberately does not add, and how every new piece attaches to the
existing pipeline without a rewrite.

## 1. Locked decisions

The owner settled the seven questions that changed implementation, not just wording. They are binding for
this release; a change to any of them needs a new decision recorded here.

| # | Decision | Consequence in the code |
| --- | --- | --- |
| 1 | **No human reviewers in the standard workflow.** The AI Judge verdict is the standard verdict source; blind human review stays as an optional calibration channel (`fixjury review …` keeps working untouched). | Evaluation verdicts are persisted in `evaluation_results`; `verdicts.human_*` is never required and never blocks a report. Metric snapshots gain a `verdict_source` dimension, so an AI-only series is never silently mixed with the historical human series. |
| 2 | **Corpus expansion is deferred.** V1.1 keeps the existing 32-task frozen corpus. | §3.1 shrinks to metadata standardisation of the current tasks. No task file is added or edited — the corpus is frozen (AGENTS.md invariant 9). |
| 3 | **Judge runs on the same opencode-go subscription with a different, stronger model**, model selectable by configuration. | Judge provider is a separate settings block (`judge_*`) over the existing HTTP helper; a judge model must be configured explicitly and is recorded per assessment. Independence is "separate model, separate session, separate context" — *not* provider independence, and the report says so. |
| 4 | **Adapter SDK is a wrapper, not a rewrite.** | New `agents/sdk.py` exposes `prepare/execute/collect/terminate/get_usage` over the existing `AgentAdapter.run()`; baseline, mini and OpenHands keep their current shape. |
| 5 | **Checks stay the current three plus coverage.** No bandit, no semgrep, no integration-test runner in V1.1. | `CheckTool` gains `coverage`. Security is judge-reported and can never become a *confirmed* automated failure; the critical-failure rules are limited to what a check can prove (failed test, regression, forbidden path, policy violation, unbuildable diff). |
| 6 | **GitHub App and GitHub Review are deferred** out of V1.1. | Phase 4 is not implemented. No organization, repository, installation or pull-request tables yet; `evaluation_runs` carries a polymorphic subject so a PR evaluation can be added later without a migration of existing rows. |
| 7 | **Plan approval covers the migrations and dependencies the plan names.** | The Alembic revisions in `migration-map.md` and the evaluation/judge modules are pre-approved. Anything outside that list — a new runtime dependency, a change to a published metric definition, a sandbox limit change — still needs its own approval. |

Two documentation defects were fixed with the approval: `PLAN_V1.1.md` pointed at a non-existent `spec.md`
and its phase-0 deliverables read as if `docs/PLAN.md` did not exist.

## 2. Scope of V1.1 after the decisions

| Plan phase | V1.1 | Note |
| --- | --- | --- |
| 0. Audit | yes | this document set |
| 1. Evaluation Core | yes | contracts, verification pipeline, scoring, persistence |
| 2. AI Judge | yes | single / adaptive / multi, escalation, observability, calibration |
| 3. Benchmark expansion | partial | no corpus growth, no new agents; SDK wrapper, orchestration, leaderboard over persisted evaluations |
| 4. GitHub Review | **no** | deferred with the GitHub App (decision 6) |
| 5. Gates and analytics | partial | analytics and agent attribution yes; repository-level quality policies follow GitHub Review |
| 6. API and dashboard | yes | evaluations, judge configuration, leaderboard, analytics; loopback-only until auth exists |
| 7. Security and release | yes | as written |

## 3. Module layout

Existing modules are extended in place; new capabilities get their own package.

```text
src/fixjury/
├── evaluation/            new — shared contracts and scoring, no I/O
│   ├── contracts.py       EvaluationRequest/Context/Finding/Score/Result/Configuration
│   ├── checks.py          CheckOutcome → structured CheckResult (incl. coverage)
│   └── scoring.py         criteria, weights, critical rules, profile version
├── judge/                 new — AI Judge, provider-agnostic
│   ├── provider.py        JudgeProvider protocol + opencode-go implementation
│   ├── schema.py          validated judge response model
│   ├── evaluator.py       context construction, prompt, validation, persistence
│   ├── modes.py           single / adaptive / multi
│   ├── escalation.py      triggers, rounds, budgets
│   ├── aggregate.py       final verdict policy
│   └── prompts/           judge_evaluate.md, judge_escalate.md (versioned)
├── verify/                extended — coverage check, structured outcomes persisted
├── metrics/               extended — verdict source, judge and evaluation cost split
├── agents/sdk.py          new — thin lifecycle wrapper over AgentAdapter
└── cli.py                 extended — judge / evaluation / leaderboard commands
```

`review/github/` from SPEC §13 is **not** created in V1.1 (decision 6).

## 4. Evaluation contracts

Pydantic models, versioned with an explicit `schema_version` literal so a stored result can be read back by
the version that wrote it.

| Contract | Fields (minimum) |
| --- | --- |
| `EvaluationRequest` | `source` (`benchmark`), `task_id`, `run_id`, `diff`, `diff_sha256`, `base_commit`, `spec_text`, `checks`, `config` |
| `EvaluationContext` | everything above plus `repository_files` (paths and sizes only), `heldout_summary` (pass/fail, never test source), `limitations` |
| `CheckResult` | `name`, `tool`, `status` (`PASSED`/`FAILED`/`WARNING`/`SKIPPED`/`ERROR`), `exit_code`, `summary`, `artifact_ref`, `duration_ms` |
| `EvaluationFinding` | `severity`, `category`, `description`, `evidence_ref`, `path`, `line`, `impact`, `confirmed`, `source` (`automated` / `judge`) |
| `CriterionScore` | `criterion`, `score` (0–100), `weight`, `rationale`, `evidence_refs` |
| `EvaluationScore` | `overall`, `criteria`, `profile`, `profile_version` |
| `EvaluationResult` | `verdict` (`ACCEPTED`/`REJECTED`/`INCONCLUSIVE`), `score`, `confidence`, `findings`, `assessments`, `limitations`, `escalated`, `timings`, `usage`, `config_hash`, `judge_config_hash` |
| `EvaluationConfiguration` | `mode`, judge models, thresholds, budgets, profile name, `max_judges`, `max_escalation_rounds`, `timeout_s`, `fallback` |

Rules that the contracts must enforce, not merely document:

- A `judge` finding without `evidence_ref` is rejected at validation time.
- `confirmed=true` is reserved for automated checks; the validator refuses it on a judge finding.
- `verdict=ACCEPTED` is impossible when any automated check is `FAILED` or `ERROR` — enforced in
  `aggregate.py`, and asserted again when a result is persisted.
- `INCONCLUSIVE` is the outcome of missing evidence, exhausted budget, or an unavailable provider. It is
  never silently rewritten to `REJECTED` or `ACCEPTED`.

## 5. Scoring

- Default profile weights exactly as SPEC §5 (Correctness 30, Regression Safety 20, Code Quality 15,
  Maintainability 10, Security 10, Minimality 5, Requirement Compliance 10).
- Profiles live in `config/evaluation.yaml` with a `profile_version`; the version is stored on every result.
  Changing a weight changes the version, so scores across versions are not compared silently.
- Scoring is deterministic: same checks, same judge assessments, same profile → same number. Ordering inside
  aggregation is fixed (sorted by criterion name), never dict-iteration order.
- Critical-failure rules run **before** weighting and can only deny acceptance, never grant it.
- Judge contributes to Code Quality, Maintainability, Security, Minimality and Requirement Compliance;
  Correctness and Regression Safety are dominated by the automated evidence, and the judge may only *lower*
  them with an evidenced finding.

## 6. AI Judge

- One `JudgeProvider` protocol (`evaluate`, `validate_response`, `get_usage`, `health_check`); the first
  implementation talks to the existing provider over `llm.complete`, which already sends the required
  session header. No new runtime dependency.
- Judge context excludes held-out test source, agent identity, agent reasoning and credentials. The patch is
  treated as untrusted input: repository instructions and code comments inside the diff are quoted as data
  and never followed (prompt states this explicitly and the prompt version is recorded).
- Modes: **single** (one assessment → verdict), **adaptive** (primary assessment, escalation triggers,
  aggregate), **multi** (all configured judges, disagreement detection, policy aggregation). Default is
  adaptive per SPEC §4.2; single must be able to run the whole platform.
- Escalation triggers: confidence below threshold, suspected critical security issue, contradictory automated
  evidence, correctness not established, critical disagreement, missing evidence. Every trigger records a
  `judge_escalations` row with the round, the trigger and the reason.
- Budgets: token budget, monetary budget, timeout and `max_judges` are enforced *around* the call, not after
  it: exhausting a budget yields `INCONCLUSIVE`, never acceptance.
- Observability per assessment: provider, model, prompt version, configuration hash, input/output tokens,
  cost with `pricing_version`, latency, confidence, escalation history, final verdict.
- Calibration: the frozen 32-task corpus plus every reviewed pair is the calibration set. Rates are measured
  against **independently verified labels** only. Neither a passing check nor a difference from the reference
  patch is a label: a correct patch can differ from the reference and an incorrect one can too, and a patch
  that passes its tests can still be wrong. With decision 1 the labels are sparse, so false acceptance and
  false rejection are computed only over cases that carry one (a human verdict on that exact pair, or a
  curated label), and unlabeled cases are counted, excluded from the rate and reported as excluded. A judge
  that accepts an unlabeled patch is not scored as correct. Agreement, confidence calibration and escalation
  frequency are computed in `metrics/compute.py` from persisted rows.

## 7. Verification pipeline additions

- `coverage` joins the whitelisted tools. The check runs in the sandbox like the others
  (`coverage run -m pytest …` is *not* introduced — the task's own `checks` decide what runs; V1.1 adds the
  tool, the parser and the artifact, not new commands in every task).
- Coverage is recorded as a `CheckResult` with the measured percentage in `summary` and the report text as
  the artifact; absence of a coverage report is `SKIPPED`, not `PASSED`.
- Structured check results are persisted for the Judge and for the report. The 2000-character tail stays as
  an artifact, not as the only evidence.
- No security scanner is added (decision 5), so `Security` cannot produce a confirmed critical failure. The
  gate says `INCONCLUSIVE`/warns instead of inventing a confirmed vulnerability.

## 8. Persistence

New tables only; no existing table changes shape except additive nullable columns where noted in
[`migration-map.md`](migration-map.md).

| Table | Role |
| --- | --- |
| `evaluation_runs` | one evaluation of one subject (`source`, `task_id`, `run_id`, `config`, `config_hash`, `scoring_version`, `judge_config_hash`, status, timings, tokens, cost, `pricing_version`) |
| `evaluation_results` | verdict, overall score, confidence, criterion scores, reasoning summary, limitations, `escalated` |
| `evaluation_findings` | severity, category, description, evidence ref, path/line, impact, `confirmed`, `source` |
| `judge_configurations` | mode, provider, models, thresholds, budgets, policy, `config_hash` |
| `judge_assessments` | per-judge verdict, score, confidence, raw response, tokens, cost, latency, prompt version |
| `judge_escalations` | round, trigger, from/to assessment, reason |

Deferred entities from SPEC §8 — `organizations`, `repositories`, `github_installations`, `pull_requests`,
`agent_versions`, `usage_records`, `quality_policies` — are not created in V1.1. `evaluation_runs.source`
plus a nullable `subject_ref` is what keeps them addable later.

## 9. Metrics and reports

- `verdict_source` becomes part of the metric configuration hash: `judge` snapshots and `human` snapshots are
  separate series and are never averaged together.
- New metrics from SPEC §6.4 on top of the current set: judge acceptance rate, regression-free judge
  acceptance, judge cost per accepted fix, tokens per accepted fix including judge tokens (reported as a
  separate column), escalation frequency, false acceptance / false rejection on the calibration set, and
  inter-judge agreement for multi mode.
- Existing definitions do not change: reviewed-pair denominators, `null` for a zero denominator, `list_price`
  and `amortized` labelled with `pricing_version`, median repair time from run timestamps.
- Agent cost stays comparable across agents; evaluation cost is reported separately and never folded into
  `cost_per_accepted_fix` without a label, because the same judge charge applies to every agent.
- Leaderboard entries reference `corpus_version` (from `benchmarks/index.json`), `config_hash` and the judge
  configuration, plus sample size and interval.

## 10. CLI additions

```bash
uv run fixjury judge evaluate --run <id> [--mode single|adaptive|multi]
uv run fixjury judge validate --config <path>
uv run fixjury evaluation show --run <id>
uv run fixjury leaderboard [--basis list_price|amortized]
```

`fixjury review policy --repo <name>` and `fixjury review github --pr <url>` are **not** added (decision 6).
Existing commands keep their names, options and behaviour (SPEC §12, PLAN_V1.1 §11).

## 11. Compatibility and limits

- The V1 pipeline stays runnable end to end with no judge configured: verification, human review, metrics,
  report and dashboard keep working. Judge is additive, not a new prerequisite.
- A judge evaluation reads only persisted rows (diff, task spec, check results); it never re-runs the agent
  and never touches the agent's container.
- Judge output is untrusted input: schema validation, numeric ranges, enum values and evidence references are
  checked before anything is stored.
- Determinism is claimed only for scoring given identical inputs. Model output is not deterministic and the
  report says so, with provider, model and prompt version recorded.
- Single-tenant, loopback-only API in V1.1. Exposing it publicly is a separate decision that requires auth.
