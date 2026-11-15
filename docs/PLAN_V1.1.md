# FixJury V1.1 — Implementation Plan

**Status:** Approved — 15 November 2026
**Version:** 2.1
**Based on:** [`docs/SPEC_V1.1.md`](SPEC_V1.1.md)
**Target:** FixJury V1.1

## 0. Approved scope decisions

The owner approved the plan with seven decisions that narrow the scope. They are binding; the reasoning and
the consequence in the code are recorded in [`docs/architecture/target-state.md`](architecture/target-state.md) §1.

| # | Decision |
| --- | --- |
| 1 | No human reviewers in the standard workflow: the AI Judge verdict is the standard verdict source, blind human review stays as an optional calibration channel. |
| 2 | Corpus expansion is deferred; V1.1 keeps the frozen 32-task corpus. |
| 3 | The Judge uses the same opencode-go subscription with a different, stronger, configurable model — model separation, not provider separation. |
| 4 | The Agent Adapter SDK is a wrapper over the existing `AgentAdapter.run()`, not a rewrite of the adapters. |
| 5 | Checks stay `pytest`, `ruff`, `mypy` plus `coverage`; no security scanners in V1.1. |
| 6 | GitHub App and Phase 4 (GitHub Review) are deferred out of V1.1. |
| 7 | Plan approval covers the migrations and dependencies this plan names; anything else still needs its own approval. |

Approved scope by phase: 0 yes, 1 yes, 2 yes, 3 partial (no corpus growth, no new agents), 4 no, 5 partial
(analytics and attribution yes, repository policies deferred with phase 4), 6 yes, 7 yes.

## 1. Implementation Strategy

Implementation follows three major stages:

- Build the shared Evaluation Core and AI Judge Engine.
- Extend the existing Benchmark functionality.
- Implement GitHub Review using the shared evaluation infrastructure.

The existing codebase must be audited before introducing new modules or changing existing interfaces.

### Core principles

- Preserve existing working functionality.
- Avoid unnecessary rewrites.
- Implement incrementally.
- Keep modules independently testable.
- Use shared evaluation contracts.
- Introduce database changes through migrations.
- Validate each milestone before proceeding.
- Keep all AI evaluations reproducible within documented provider limitations.

### Dependency order

```text
Existing Codebase Audit
        |
        v
Evaluation Contracts
        |
        v
Evaluation Core
        |
        v
AI Judge Engine
        |
        +------------------+
        |                  |
        v                  v
Benchmark           GitHub Review
        |                  |
        v                  v
Leaderboard         Quality Gates
        |                  |
        +--------+---------+
                 |
                 v
          Shared Analytics
                 |
                 v
             V1.1 Release
```

Benchmark and GitHub Review can be developed in parallel after the Evaluation Core is stable.

## 2. Phase 0 — Existing Codebase Audit

**Priority:** P0
**Estimated duration:** 3–5 days

### Objective

Understand the current implementation and identify reusable components before changing the architecture.

### Tasks

#### 0.1 Repository audit

- Inspect the complete repository structure.
- Read AGENTS.md.
- Inspect existing CLI commands.
- Inspect current benchmark implementation.
- Inspect agent adapters.
- Inspect verification pipeline.
- Inspect metrics calculation.
- Inspect database models and migrations.
- Inspect API and dashboard.
- Inspect Docker execution environments.
- Identify existing technical debt.

#### 0.2 Architecture mapping

Document:

- Existing execution lifecycle.
- Existing task lifecycle.
- Existing result format.
- Existing database relationships.
- Existing worker infrastructure.
- Existing configuration management.
- Existing testing conventions.

#### 0.3 Compatibility analysis

Identify which existing modules can be extended and which require refactoring.

### Deliverables

- docs/architecture/current-state.md
- docs/architecture/target-state.md
- docs/architecture/migration-map.md

These documents are written as a *current-state* audit that references `docs/PLAN.md` rather than restating
it, so the V1 record keeps one home.

### Acceptance criteria

- All major existing modules are documented.
- Existing CLI behavior is documented.
- Reusable components are identified.
- Architectural conflicts are recorded.
- No implementation changes are made during this phase.

## 3. Phase 1 — Evaluation Core

**Priority:** P0
**Estimated duration:** 2 weeks

### Objective

Create a shared evaluation infrastructure used by Benchmark and GitHub Review.

### 1.1 Evaluation contracts

Implement unified schemas for:

- EvaluationRequest
- EvaluationContext
- VerificationResult
- EvaluationFinding
- EvaluationScore
- EvaluationResult
- EvaluationConfiguration

Requirements:

- Pydantic validation.
- Versioned schemas.
- Stable serialization.
- Explicit verdict types.
- Consistent timestamps.
- Traceable execution IDs.

### 1.2 Verification pipeline

Implement or extend:

- Unit test execution.
- Integration test execution.
- Regression testing.
- Static analysis.
- Type checking.
- Security checks.
- Diff analysis.
- Test coverage analysis.

Each check must produce a structured result.

Possible statuses:

- PASSED
- FAILED
- WARNING
- SKIPPED
- ERROR

The pipeline must distinguish between a failed check and a check that could not execute.

### 1.3 Scoring engine

Implement:

- Criterion-specific scoring.
- Configurable weights.
- Critical failure rules.
- Score aggregation.
- Evaluation profiles.
- Scoring version tracking.

Initial criteria:

- Correctness.
- Regression Safety.
- Code Quality.
- Maintainability.
- Security.
- Minimality.
- Requirement Compliance.

Scoring must be deterministic for identical inputs and identical configuration.

### 1.4 Evaluation persistence

Implement database persistence for:

- Evaluation runs.
- Evaluation results.
- Findings.
- Verification artifacts.
- Scoring configuration.
- Evaluation metadata.

Use Alembic migrations.

### Acceptance criteria

- All evaluation results use a common schema.
- Existing verification functionality remains operational.
- Scoring is deterministic.
- Critical failures cannot be overridden by Judge.
- Evaluation results can be persisted and retrieved.
- Unit and integration tests pass.

## 4. Phase 2 — AI Judge Engine

**Priority:** P0
**Estimated duration:** 2–3 weeks

### Objective

Implement an independent AI evaluation system with configurable single, adaptive and multi-Judge modes.

### 2.1 Provider abstraction

Implement a common Judge provider interface.

Required operations:

- evaluate()
- validate_response()
- get_usage()
- health_check()

Initial implementation should support one provider.

Additional providers must be possible without changing Evaluation Core.

### 2.2 Judge evaluator

Implement:

- Evaluation context construction.
- Prompt management.
- Structured model output.
- Response validation.
- Evidence validation.
- Score normalization.
- Verdict generation.
- Evaluation persistence.

Prompts must be versioned and stored separately from application code.

### 2.3 Evaluation modes

Implement three modes.

**Single**

- Execute one Judge.
- Validate its response.
- Produce final verdict.

**Adaptive**

- Execute primary Judge.
- Evaluate escalation conditions.
- Invoke additional Judges when required.
- Aggregate independent assessments.
- Apply final verdict policy.

**Multi**

- Execute all configured Judges.
- Compare their assessments.
- Detect disagreements.
- Aggregate results.

### 2.4 Escalation engine

Implement configurable triggers:

- Low confidence.
- Critical findings.
- Contradictory automated evidence.
- Uncertain correctness.
- Significant disagreement.
- Insufficient evaluation evidence.

Configuration:

- Maximum Judges.
- Maximum escalation rounds.
- Confidence threshold.
- Risk threshold.
- Disagreement threshold.
- Token budget.
- Monetary budget.
- Timeout.
- Fallback policy.

### 2.5 Verdict aggregation

Implement final verdict policies.

Possible outcomes:

- ACCEPTED
- REJECTED
- INCONCLUSIVE

Critical verified failures must prevent acceptance.

Insufficient evidence, exhausted budgets and unavailable providers must not silently produce acceptance.

### 2.6 Judge observability

Record:

- Provider.
- Model.
- Model version when available.
- Prompt version.
- Configuration hash.
- Input and output tokens.
- Cost.
- Latency.
- Confidence.
- Escalation history.
- Final verdict.

### 2.7 Judge calibration

Create a calibration dataset containing known examples of:

- Correct patches.
- Incorrect patches.
- Regressions.
- Unnecessary changes.
- Security issues.
- Ambiguous tasks.

Measure:

- False acceptance rate.
- False rejection rate.
- Inter-Judge agreement.
- Confidence calibration.
- Escalation frequency.

### Acceptance criteria

- Single mode works with one Judge.
- Adaptive mode can invoke additional Judges.
- Multi mode supports independent evaluations.
- All modes respect budget and timeout limits.
- Invalid responses are rejected.
- Critical automated failures cannot be overridden.
- Insufficient evidence produces INCONCLUSIVE.
- Every evaluation has an auditable record.
- Judge calibration tests pass defined thresholds.

## 5. Phase 3 — Benchmark Expansion

**Priority:** P0
**Estimated duration:** 2–3 weeks

### Objective

Make FixJury Benchmark a reproducible platform for comparing AI coding agents.

### 3.1 Dataset expansion

**Deferred by decision 2**: V1.1 keeps the frozen 32-task corpus and adds no tasks. Standardising metadata
on an existing frozen task would edit `task.yaml`, which AGENTS.md invariant 9 forbids once a run exists, so
this task reduces to documenting the metadata that is already there and validating it.

- Audit existing benchmark tasks.
- Document difficulty classification and task categories as recorded today.
- Confirm `corpus_version` and the frozen revision used by every leaderboard entry.
- Record repository licenses (already stored on `tasks.license`).
- Validate task reproducibility with the no-op baseline check.

### 3.2 Agent Adapter SDK

Implement a common adapter interface.

Required methods:

- prepare()
- execute()
- collect()
- terminate()
- get_usage()

Extend existing adapters rather than replacing them.

### 3.3 Agent integrations

Prioritize:

- Existing baseline.
- Existing mini-SWE-agent integration.
- OpenHands.
- Additional configurable coding agents.

Every integration must expose execution limits and usage information.

### 3.4 Experiment orchestration

Implement:

- Experiment creation.
- Task scheduling.
- Agent execution.
- Timeout management.
- Artifact collection.
- Verification.
- Judge evaluation.
- Result persistence.

Ensure comparable conditions across agents.

### 3.5 Leaderboard

Implement:

- Overall rankings.
- Language rankings.
- Task category rankings.
- Difficulty rankings.
- Cost comparisons.
- Repair time comparisons.
- Regression-free acceptance rate.
- Sample size and uncertainty.

Leaderboard entries must reference an immutable benchmark revision and evaluation configuration.

### Acceptance criteria

- Existing benchmark workflow remains functional.
- At least three agent configurations can be evaluated.
- Benchmark runs are reproducible within documented limits.
- Agent configurations are persisted.
- Results use the shared Evaluation Core.
- Leaderboard metrics are generated from persisted results.

## 6. Phase 4 — GitHub Review

**Deferred out of V1.1 by decision 6.** No GitHub App, no webhook endpoint, no repository cloning, and none
of the organization/repository/installation/pull-request tables are created in this release. The only
obligation V1.1 carries is the one this section's acceptance criteria would have imposed later: the shared
Evaluation Core must not assume a benchmark-shaped subject, so `evaluation_runs` carries `source` plus a
nullable `subject_ref` (see `docs/architecture/target-state.md` §8). Re-read this section when the GitHub App
work is scheduled.

**Priority:** deferred
**Estimated duration:** 3 weeks when scheduled

### Objective

Enable automatic evaluation of real GitHub Pull Requests.

### 4.1 GitHub App

Implement:

- GitHub App authentication.
- Installation management.
- Repository permissions.
- Webhook verification.
- Pull Request event processing.
- Commit tracking.

Required events:

- pull_request.opened
- pull_request.synchronize
- pull_request.reopened

Webhook processing must be idempotent.

### 4.2 Pull Request ingestion

Implement:

- PR metadata retrieval.
- Base commit retrieval.
- Head commit retrieval.
- Diff retrieval.
- Changed file collection.
- Repository snapshot creation.

Every evaluation must be associated with a specific immutable head commit.

### 4.3 Isolated execution

Implement secure execution workers.

Requirements:

- One isolated environment per execution.
- CPU and memory limits.
- Execution timeout.
- Restricted network access.
- No access to platform credentials.
- Clean environment after execution.
- Artifact collection.

Repository code and PR content must be treated as untrusted.

### 4.4 Review pipeline

Connect GitHub Review to Evaluation Core.

Pipeline:

- Receive PR event.
- Retrieve repository snapshot.
- Execute verification checks.
- Collect artifacts.
- Invoke AI Judge.
- Generate evaluation report.
- Publish GitHub Check Run.
- Publish inline comments where applicable.

### 4.5 GitHub reports

Include:

- Overall score.
- Final verdict.
- Test results.
- Security findings.
- Regression findings.
- Code quality findings.
- Judge explanation.
- Evaluation cost.
- Execution time.

### Acceptance criteria

- GitHub App can be installed.
- Pull Requests trigger evaluations.
- Duplicate webhook events do not create duplicate runs.
- New commits trigger fresh evaluations.
- Evaluation uses the correct base and head commits.
- Reports are published successfully.
- Execution remains isolated.
- No automatic merge is performed.

## 7. Phase 5 — Quality Gates and Analytics

**Priority:** P1
**Estimated duration:** 2 weeks

### Objective

Provide configurable quality policies and centralized analytics.

### 5.1 Quality Gates

**Repository-level policies are deferred with Phase 4** (decision 6): without GitHub Review there is no
repository to gate. V1.1 implements gates over an evaluation configuration — the benchmark/agent profile —
using the same PASS / FAIL / INCONCLUSIVE outcomes, and reuses them unchanged once a repository policy exists.

Implement repository-level policies:

- Minimum quality score.
- Required test suites.
- Security thresholds.
- Regression policies.
- Forbidden file paths.
- Maximum change size.
- Judge mode.
- Evaluation budget.

Support:

- PASS
- FAIL
- INCONCLUSIVE

Quality Gates must distinguish confirmed automated failures from AI-generated warnings.

### 5.2 Organization analytics

Implement:

- Evaluation history.
- Agent performance.
- Cost analytics.
- Token consumption.
- Repair time.
- Regression statistics.
- Judge performance.
- Escalation statistics.
- False acceptance and rejection rates.

### 5.3 Agent attribution

Support explicit attribution through:

- Agent integrations.
- Agent metadata.
- API registration.
- CI integration.

Unknown attribution must remain explicitly unknown.

### Acceptance criteria

- Repository policies are configurable.
- Quality Gates use shared evaluation results.
- Organization data is isolated.
- Metrics can be filtered by repository, agent and time period.
- Costs and evaluation usage are recorded.

## 8. Phase 6 — API and Dashboard

**Priority:** P1
**Estimated duration:** 2 weeks

### Objective

Expose the platform functionality through a stable API and user interface.

### 6.1 API

Implement endpoints for:

- Organizations.
- Repositories.
- Agents.
- Benchmark tasks.
- Benchmark experiments.
- Pull Requests.
- Evaluations.
- Judge configurations.
- Quality policies.
- Analytics.
- Leaderboard.

Requirements:

- Pydantic schemas.
- Authentication.
- Authorization.
- Pagination.
- Consistent error responses.
- API versioning.
- OpenAPI documentation.

### 6.2 Dashboard

Implement pages:

- Overview.
- Benchmark.
- Leaderboard.
- Agents.
- Pull Requests.
- Evaluation Details.
- Judge Configuration.
- Quality Policies.
- Cost Analytics.

The interface must display evaluation evidence and explanations, not only aggregate scores.

### Acceptance criteria

- Core workflows are accessible through the API.
- Dashboard displays persisted evaluation data.
- Organization permissions are enforced.
- Evaluation details are traceable to original execution artifacts.

## 9. Phase 7 — Security, Reliability and Release

**Priority:** P0
**Estimated duration:** 1–2 weeks

### Objective

Prepare FixJury V1.1 for controlled production use.

### 7.1 Security audit

Verify:

- Docker isolation.
- Network restrictions.
- Credential protection.
- Webhook verification.
- Organization isolation.
- API authorization.
- Secret handling.
- Prompt injection resistance.
- Artifact access controls.

### 7.2 Reliability

Implement or validate:

- Retry policies.
- Provider failure handling.
- Queue recovery.
- Idempotent execution.
- Timeout handling.
- Budget enforcement.
- Evaluation state recovery.

### 7.3 Observability

Ensure visibility into:

- API requests.
- Worker execution.
- Queue latency.
- Judge latency.
- Provider errors.
- Evaluation failures.
- Token consumption.
- Monetary costs.

### 7.4 Release

- Run full test suite.
- Run migrations on a clean database.
- Validate Docker Compose deployment.
- Validate CLI compatibility.
- Update documentation.
- Publish deployment instructions.
- Prepare release notes.

### Acceptance criteria

- All required tests pass.
- Security requirements are verified.
- Existing functionality remains compatible.
- Evaluation failures are observable.
- Production configuration is documented.
- V1.1 deployment can be reproduced.

## 10. Task Dependencies

| Phase                | Depends on          | Parallelizable        |
| -------------------- | ------------------- | --------------------- |
| 0. Audit             | —                   | No                    |
| 1. Evaluation Core   | Phase 0             | No                    |
| 2. AI Judge          | Phase 1             | No                    |
| 3. Benchmark         | Phase 2             | Partially             |
| 4. GitHub Review     | Phase 2             | Partially             |
| 5. Quality Gates     | Phase 4             | Yes, with analytics   |
| 6. API and Dashboard | Phases 3–5          | Yes                   |
| 7. Release           | All required phases | No                    |

Benchmark and GitHub Review can be developed by separate workstreams after the shared core and Judge interfaces stabilize.

With decision 6 the graph shortens: phase 4 is out, phase 5 keeps analytics and attribution, and phase 6
depends on phases 2–3 and the analytics part of 5.

## 11. Global Engineering Rules

- Read AGENTS.md before making changes.
- Inspect existing code before creating new modules.
- Prefer extending existing implementations.
- Never introduce duplicate evaluation pipelines.
- Keep AI Judge providers replaceable.
- Keep prompts version-controlled.
- Use migrations for database changes.
- Add tests with every feature.
- Preserve existing CLI behavior.
- Do not silently change benchmark scoring.
- Do not expose hidden tests.
- Never run untrusted code outside isolated workers.
- Record all evaluation failures.
- Do not treat missing evidence as successful evaluation.
- Update documentation when behavior changes.

## 12. Definition of Done

A task is complete only when:

- Implementation satisfies its acceptance criteria.
- Relevant unit tests exist.
- Relevant integration tests pass.
- Type checking passes.
- Linting passes.
- Existing functionality is not unintentionally broken.
- Security implications are addressed.
- Documentation is updated.
- Database changes include migrations where necessary.

Required verification:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
```

Frontend changes must additionally pass the existing frontend typecheck and build commands.

## 13. Estimated Timeline

| Phase                        | Duration  |
| ---------------------------- | --------- |
| Codebase Audit               | 3–5 days  |
| Evaluation Core              | 2 weeks   |
| AI Judge Engine              | 2–3 weeks |
| Benchmark Expansion          | 2–3 weeks |
| GitHub Review                | 3 weeks   |
| Quality Gates and Analytics  | 2 weeks   |
| API and Dashboard            | 2 weeks   |
| Security and Release         | 1–2 weeks |

Estimated total: 15–19 weeks for a mostly sequential implementation.

Parallel workstreams may reduce calendar time but do not eliminate integration and validation work.

## 14. Implementation Execution Protocol

Implementation must follow this sequence:

- Read and understand [`SPEC_V1.1.md`](SPEC_V1.1.md).
- Read and understand this implementation plan.
- Inspect the existing repository.
- Confirm the task dependencies.
- Select the next unblocked task.
- Implement only that task or a clearly defined small group of tasks.
- Run relevant tests.
- Review compatibility and security.
- Update implementation documentation.
- Mark the task complete only after verification.

Do not implement future phases prematurely.

Do not rewrite the existing architecture without a documented justification.

If the existing repository conflicts with this plan, document the conflict and propose the smallest safe adjustment before proceeding.

End of Implementation Plan.
