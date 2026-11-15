# FixJury V1.1 — Migration Map

**Baseline:** [`current-state.md`](current-state.md) at `c5fc958`.
**Target:** [`target-state.md`](target-state.md) (decisions of 15 November 2026 applied).
**Rule:** every existing module is *extended* unless this document names a reason to do otherwise. Nothing in
V1 is deleted; the benchmark that is mid-flight stays runnable while V1.1 lands.

## 1. Module map

| Existing | V1.1 destination | Action | Risk |
| --- | --- | --- | --- |
| `verify/verdict.py::CheckOutcome`, `VerificationResult` | `evaluation/checks.py::CheckResult` | extend: normalise outcomes, keep the existing booleans | a second source of truth if the booleans are recomputed — they are derived, never stored twice |
| `tasks/schema.py::CheckTool` | same enum + `coverage` | extend | tasks are frozen; adding a tool does not edit a task file, so comparability holds |
| `verify/runner.py` | same, plus coverage probe and artifact capture | extend | install/check containers must keep `network=none` for checks |
| `verify/service.py::store_verdict` | unchanged; a new `evaluation/store.py` writes evaluation rows | extend beside | verdict and evaluation must be written in one transaction for a run, or a half-evaluated run is readable |
| `metrics/compute.py` | same functions + `verdict_source` dimension and judge metrics | extend | changing a definition changes the hash; snapshots are never recomputed in place |
| `metrics/service.py` | same, snapshot payload gains `verdict_source`, judge/evaluation cost split | extend | old snapshots stay valid as their own series |
| `llm.py` | unchanged; wrapped by `judge/provider.py` | wrap | the provider's session header must stay in one place |
| `agents/base.py` | unchanged protocol; `agents/sdk.py` wraps it | wrap | the wrapper must not gain state or DB access |
| `config.py` | new `judge_*` settings block | extend | secrets stay in `.env`; no default model, so a missing judge model fails loudly |
| `cli.py` | new `judge`, `evaluation`, `leaderboard` commands | extend | existing commands untouched |
| `prompts/*.md` | `judge/prompts/*.md` added, with a version constant | new | prompts are benchmark decisions: a change is called out in the pull request |
| `api/routes.py` | evaluation endpoints added in phase 6 | extend | no auth yet; loopback only |
| `models.py` + `migrations/` | six new tables and their enums | new | downgrade must drop the enum types too |
| GitHub / organizations / repositories | deferred (decision 6) | defer | `evaluation_runs` stays polymorphic so the tables can be added without rewriting rows |

## 2. Schema migration

One Alembic revision per coherent group, each with a working `downgrade()` that drops indexes, tables and
the PostgreSQL enum types it created (AGENTS.md pitfall: enum types outlive their tables).

**Revision `0003_verdict_evidence`**

- `verdicts` gains one additive `check_results` JSONB column, defaulting to an empty list: the structured
  evidence a judge must cite cannot be re-derived from five booleans, and it belongs to the verification
  rather than to one evaluation. Old rows carry `[]`, which says "verified before this column existed"
  instead of pretending the checks printed nothing.
- no table is touched otherwise; no historical row is rewritten.

**Revision `0004_evaluation_core`**

- enums: `evaluation_source`, `evaluation_status`, `evaluation_verdict`, `criterion`, `finding_severity`,
  `finding_source`
- tables: `evaluation_runs`, `evaluation_results`, `evaluation_findings`
- indexes: `evaluation_runs(run_id)`, `evaluation_runs(source, subject_ref, created_at)`,
  `evaluation_findings(evaluation_run_id, severity)`
- `evaluation_runs.judge_config_hash` is nullable: phase 1 records the evaluation configuration hash, phase 2
  starts recording a named judge configuration
- no column is dropped or rewritten; no historical row is touched

**Revision `0006_evaluation_error`**

- `evaluation_runs` gains one nullable `error` column: `status = failed` said an evaluation
  failed, and nothing said why. A benchmark that judges every pair has to tell a provider
  timeout from a run that carried no evidence to judge, and a log line is not a database row.
- no table is touched otherwise; no historical row is rewritten. `scoring_version` stays NOT
  NULL: a failed evaluation stores the literal `unscored`, which is what it was.

**Revision `0005_judge_assessments`**

- tables: `judge_configurations`, `judge_assessments`, `judge_escalations`
- indexes: `judge_assessments(evaluation_run_id)`, `judge_escalations(evaluation_run_id)`
- `judge_configurations` is created here rather than with the evaluation tables: an empty table nothing writes
  is not a migration, it is a placeholder for a later phase

`verdicts` keeps its V1 meaning: `human_*` / `reviewer_*` are untouched and a machine verdict never overwrites
them. What it gains is `check_results`, the structured evidence; the verdict itself lives in
`evaluation_results`, which is what makes decisions 1 and 6 additive rather than destructive.

**Metric snapshots** are not migrated. `verdict_source` is written into the payload of new snapshots; a
snapshot without it is, by definition, a `human` series.

## 3. Data flow after the change

```text
run (agent)
  └─ verify ─────────────► verdicts + rejections        (automated evidence, unchanged)
        └─ CheckResult[] ─► evaluation/checks           (structured, persisted)
              └─ evaluate ► judge/provider ─► judge_assessments (+ escalations)
                    └─────► evaluation_results + findings
                          └─ metrics.compute ─► metric_snapshots(verdict_source)
                                └─ report / dashboard / api / leaderboard
```

`fixjury verify` keeps working alone: without a judge configured, the evaluation step is skipped and the V1
pipeline renders exactly as today. `fixjury judge evaluate --run <id>` works only against a verdict that
carries `check_results`: a judge never re-runs an agent, and it never evaluates nothing. A run verified before
revision `0003_verdict_evidence` has five booleans and no stored check output, so it is reported as
**unevaluable** and no `evaluation_results` row is written. Re-running `fixjury verify` on that run rewrites
the automated evidence — and only that: `store_verdict` updates the row in place, so a human verdict, its
reviewer and its batch survive the re-verification. That is the backfill, and it is explicit rather than
silent.

## 4. Ordered tasks

Each row is one commit (Conventional Commits, one plan task per commit). Acceptance is what the commit must
demonstrate before the next one starts.

| # | Task | Touches | Acceptance |
| --- | --- | --- | --- |
| 0.1–0.3 | audit, architecture mapping, compatibility analysis | `docs/architecture/*` | this document set; no production code changed |
| 1.1 | evaluation contracts | `evaluation/contracts.py`, `tests/unit/test_evaluation_contracts.py` | schema version enforced; judge finding without evidence refused; `confirmed` refused on judge findings |
| 1.2a | coverage as a whitelisted tool | `tasks/schema.py`, `tests/unit/test_task_schema.py` | `coverage` parses; unknown flags still refused; no corpus file edited |
| 1.2b | structured check results | `evaluation/checks.py`, `verify/verdict.py`, `verify/service.py`, `migrations/versions/0003_verdict_evidence.py`, unit + integration tests | outcomes become `CheckResult` and are written to `verdicts.check_results` (revision `0003_verdict_evidence`); `ERROR` is distinct from `FAILED`; re-verification preserves the review fields, so it is the backfill for older runs |
| 1.3 | scoring engine and profiles | `evaluation/scoring.py`, `config/evaluation.yaml`, `tests/unit/test_evaluation_scoring.py` | weights sum to 100; critical failure denies acceptance; deterministic ordering; profile version stored |
| 1.4 | persistence | `models.py`, `migrations/versions/0004_evaluation_core.py`, `evaluation/store.py`, `tests/integration/test_evaluation_store.py` | round-trip of a result with findings; downgrade/upgrade on a clean database; enum types dropped by downgrade |
| 2.1 | judge provider | `judge/provider.py`, `judge/schema.py`, settings, unit tests with a fake transport | response validated; malformed response refused; usage recorded |
| 2.2 | judge evaluator | `judge/evaluator.py`, `judge/prompts/judge_evaluate.md`, unit tests | context excludes held-out source and agent identity; prompt version recorded; evidence references validated |
| 2.3 | modes | `judge/modes.py`, unit tests | single reaches a verdict; adaptive escalates on the configured triggers; multi detects disagreement |
| 2.4 | escalation and budgets | `judge/escalation.py`, unit tests | each trigger fires once per round; exhausted budget yields `INCONCLUSIVE` |
| 2.5 | aggregation and critical rules | `judge/aggregate.py`, unit tests | `ACCEPTED` impossible with a failed automated check; `INCONCLUSIVE` never becomes `ACCEPTED` |
| 2.6 | observability | `judge/evaluator.py`, persistence, integration test | provider, model, prompt version, config hash, tokens, cost and latency stored per assessment |
| 2.7 | calibration | `metrics/compute.py`, `tests/unit/test_metrics_judge.py` | false acceptance/rejection, agreement, escalation frequency computed from rows; missing human labels reported as missing |
| 3.2 | adapter SDK wrapper | `agents/sdk.py`, unit tests | five lifecycle methods over `run()`; adapters unchanged; no DB or global config access |
| 3.4 | orchestration | `bench.py`, `worker.py`, `cli.py`, integration test | `bench --verify` optionally evaluates; a judge failure marks the evaluation failed, never the run |
| 3.5 | leaderboard | `leaderboard/`, `cli.py`, tests | entries reference `corpus_version`, `config_hash`, judge config, sample size |
| 5.1 | quality gates | `evaluation/gates.py`, `cli.py`, tests | a stored evaluation yields `PASS`/`FAIL`/`INCONCLUSIVE`; a confirmed automated failure can never pass; `INCONCLUSIVE` is its own outcome, never a pass; repository-level policy waits for phase 4 |
| 5.2 | analytics | `metrics/`, API, tests | history, agent performance, cost, escalation statistics from snapshots and evaluations |
| 5.3 | agent attribution | adapters metadata, tests | unknown attribution stays explicitly unknown |
| 6.1–6.2 | API and dashboard | `api/`, `web/`, tests + typecheck | evaluation endpoints and pages; existing pages unchanged |
| 7.x | release | docs, compose, runbook | full chain green, migration on a clean database, USAGE updated |

**Landed so far:** 0.1–0.3 (this document set), 1.1–1.4 (contracts, coverage and structured check evidence,
scoring engine, evaluation persistence — revisions `0003_verdict_evidence` and `0004_evaluation_core`) and
2.1–2.6 (provider, validated reply schema, versioned prompts, three modes, escalation and budgets,
aggregation, per-assessment observability — revision `0005_judge_assessments`). Task 2.7 (calibration
metrics) has no task where the numbers would be read yet: it lands with the metrics extension in phase 3.

## 5. Compatibility guarantees

- Every existing CLI command keeps its name, options and output shape.
- `benchmarks/**` is not edited. `coverage` is a new allowed tool, not a new requirement for existing tasks.
- `verdicts`, `rejections`, `review_batches` and `metric_snapshots` keep their V1 semantics. Human review is
  optional, not removed.
- A database with V1 rows upgrades to V1.1 with additive migrations only; `downgrade` returns to V1 tables
  without data loss for V1 entities.
- A judge-less configuration is a supported configuration: no `judge_*` setting, no evaluation rows, the V1
  report is byte-identical to today's for the same snapshot.

## 6. Risk register

| Risk | Mitigation |
| --- | --- |
| Judge model output is non-deterministic and can drift between runs | record model, prompt version and config hash per assessment; never claim reproducibility beyond "within documented provider limitations" |
| Judge inflates cost now that every pair costs two model calls | budgets per evaluation, token accounting per assessment, cost reported separately from agent cost |
| Prompt injection through the diff (repository instructions, comments) | the patch is quoted as untrusted data; the prompt says so; the judge cannot write files or call tools |
| Blind review leaks agent identity to the judge | judge context is built from persisted rows with agent fields stripped, same rule as `review/pack.py` |
| Calibration has few human labels after decision 1 | automated checks are the primary reference label; human labels are reported as a sparse additional set, never synthesised |
| Schema churn while a benchmark run is pending | additive tables only, one revision per group, migration tested both ways |
| `coverage` parsed from output text is brittle | parse the machine-readable report file; an unparsable report is `ERROR`, never `PASSED` |
| Two evaluation paths (V1 verdict, V1.1 evaluation) diverge | `evaluation_runs` references `run_id`; the report reads evaluations when present and the V1 verdict otherwise, with the source labelled |
