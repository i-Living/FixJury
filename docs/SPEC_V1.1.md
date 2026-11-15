# Spec: FixJury — V1.1

**Status:** Proposed
**Version:** 1.0
**Product:** Autonomous AI Code Evaluation Platform

## 1. Objective

FixJury is an agent-agnostic platform for evaluating the quality, reliability and cost-efficiency of AI-generated code.

The platform has two primary capabilities:

- FixJury Benchmark — objectively compares AI coding agents using a reproducible benchmark dataset.
- FixJury Review — automatically evaluates AI-generated code in real GitHub Pull Requests.

Both capabilities use a shared Evaluation Core and an independent AI Judge Engine.

The platform does not require human reviewers in its standard evaluation workflow.

### Product goals

- Measure whether AI agents actually solve programming tasks.
- Detect regressions and unintended changes.
- Evaluate maintainability, security and code quality.
- Compare agents using consistent evaluation criteria.
- Measure the real cost and time required to produce an accepted fix.
- Automatically evaluate GitHub Pull Requests.
- Provide reproducible and explainable evaluation results.

### Non-goals

- Building another AI coding agent.
- Replacing GitHub or CI/CD.
- Automatically modifying submitted patches.
- Automatically merging Pull Requests.
- Requiring human approval for ordinary evaluation.
- Training foundation models.

## 2. Capability Map

The system consists of three independently testable modules.

| Module ID       | Responsibility                                              | Depends on      |
| --------------- | ----------------------------------------------------------- | --------------- |
| evaluation-core | Verification, scoring, AI Judge and evaluation results      | —               |
| benchmark       | Reproducible comparison of coding agents                    | evaluation-core |
| github-review   | Automated evaluation of GitHub Pull Requests                | evaluation-core |

Build order:

evaluation-core → benchmark → github-review

The two product modules must share evaluation contracts, scoring definitions and result schemas.

## 3. Product Architecture

### 3.1 Evaluation Core

The central component responsible for evaluating code changes.

Responsibilities:

- Execute automated tests.
- Perform static analysis.
- Detect regressions.
- Analyze security risks.
- Evaluate code quality.
- Calculate metrics.
- Collect execution artifacts.
- Invoke AI Judge.
- Produce a unified evaluation result.

All evaluations must use a consistent result schema.

### 3.2 AI Judge Engine

AI Judge is an independent evaluation component that assesses code produced by coding agents.

It must be logically separated from the agent execution environment.

Judge receives:

- Original task description.
- Acceptance criteria.
- Base repository revision.
- Generated patch.
- Automated verification results.
- Static analysis results.
- Relevant execution logs.
- Relevant repository context.

Judge must not receive:

- Hidden test source code.
- Agent identity when it is not required for evaluation.
- Agent-specific internal reasoning.
- Credentials or secrets.
- Unnecessary information about other agents.

Judge must produce a structured evaluation with evidence and explanations.

### 3.3 FixJury Benchmark

Responsible for running identical tasks against multiple coding agents.

Features:

- Frozen benchmark dataset.
- Agent Adapter interface.
- Reproducible execution environments.
- Identical task constraints.
- Execution limits and budgets.
- Independent verification.
- AI-based evaluation.
- Public leaderboard.
- Benchmark reports.

### 3.4 FixJury Review

Responsible for evaluating real GitHub Pull Requests.

Features:

- GitHub App integration.
- Pull Request ingestion.
- Diff analysis.
- Automated verification.
- AI Judge evaluation.
- Inline review comments.
- Quality Gates.
- Evaluation reports.
- Historical analytics.

## 4. AI Judge Specification

### 4.1 Judge independence

AI Judge must be independent of the coding agent being evaluated.

It must use a separately configured model provider and evaluation context.

The Judge model should have stronger evaluation capabilities than the tested coding agent whenever possible. Model strength must be configurable rather than assumed from the model name.

### 4.2 Evaluation modes

The system must support three configurable modes.

**Single**

- One Judge evaluates the result.
- One verdict is produced.
- Lowest evaluation cost.

**Adaptive**

- One primary Judge evaluates the result.
- Additional Judges are invoked when escalation conditions are met.
- Escalation depends on risk, confidence, contradictory evidence and configured budget.

**Multi**

- Multiple independent Judges evaluate every result.
- Their assessments are aggregated.
- Disagreements are handled according to the configured resolution policy.

Default mode: adaptive.

The user must be able to switch to single and operate the entire platform with only one Judge.

### 4.3 Configuration

The following parameters must be configurable:

- Evaluation mode.
- Primary Judge provider.
- Primary Judge model.
- Secondary Judge providers and models.
- Maximum number of Judges.
- Confidence threshold.
- Risk escalation threshold.
- Disagreement threshold.
- Maximum escalation rounds.
- Evaluation timeout.
- Token budget.
- Monetary budget.
- Fallback provider and model.
- Final verdict policy.

Configuration must support global, organization and repository-level policies, with more specific settings overriding inherited defaults.

### 4.4 Escalation rules

Additional Judges may be invoked when:

- Primary Judge confidence falls below the configured threshold.
- A critical security issue is suspected.
- Automated checks produce contradictory evidence.
- Correctness cannot be established.
- Judges disagree on critical findings.
- Evaluation evidence is incomplete or ambiguous.

Escalation must respect configured limits.

If the budget or maximum number of attempts is exhausted, the evaluation must not automatically become accepted.

### 4.5 Judge output

Judge must return a validated structured response.

Required fields:

- verdict
- overall_score
- confidence
- criteria_scores
- findings
- evidence
- reasoning_summary
- limitations
- escalation_required

Supported verdicts:

- ACCEPTED
- REJECTED
- INCONCLUSIVE

Every finding must contain:

- Severity.
- Category.
- Description.
- Evidence reference.
- Relevant file and line, when applicable.
- Explanation of impact.

Judge must not report a finding without supporting evidence.

### 4.6 Judge reliability

The system must:

- Record model and provider versions.
- Record prompt and evaluation configuration hashes.
- Preserve raw structured responses.
- Validate response schemas.
- Detect malformed or incomplete responses.
- Support deterministic evaluation settings where providers permit them.
- Support independent re-evaluation.
- Track Judge disagreement and false-positive rates.
- Never allow Judge to modify the evaluated patch.

## 5. Evaluation Criteria

Evaluation must use independent criteria.

Default scoring profile:

| Criterion              | Weight |
| ---------------------- | ------ |
| Correctness            | 30%    |
| Regression Safety      | 20%    |
| Code Quality           | 15%    |
| Maintainability        | 10%    |
| Security               | 10%    |
| Minimality             | 5%     |
| Requirement Compliance | 10%    |

Weights must be configurable through evaluation profiles.

### Critical failures

The following conditions must be capable of blocking acceptance independently of the overall score:

- Critical security vulnerability.
- Failed mandatory acceptance test.
- Confirmed regression.
- Forbidden file modification.
- Violation of mandatory task constraints.

AI Judge must not override confirmed failures from mandatory automated checks.

If a result cannot be established reliably, it must be marked INCONCLUSIVE.

### Evaluation result

Every evaluation must include:

- Final verdict.
- Overall score.
- Individual criterion scores.
- Confidence.
- Critical findings.
- Automated verification results.
- Judge assessments.
- Execution time.
- Token consumption.
- Evaluation cost.
- Configuration hash.

## 6. Benchmark Requirements

### 6.1 Dataset

The benchmark must support:

- Frozen task definitions.
- Fixed base commits.
- Acceptance criteria.
- Allowed and forbidden paths.
- Public tests.
- Held-out tests.
- Tool versions.
- Difficulty classification.
- Repository license metadata.

Initial expansion targets:

- 100–300 tasks.
- Python.
- JavaScript / TypeScript.
- Go.

These targets do not require all languages to be supported in the initial release.

### 6.2 Agent Adapter

All coding agents must implement a common adapter contract.

Required operations:

- `prepare()` — initialize the execution environment.
- `execute()` — execute the repair task.
- `collect()` — collect the resulting patch and artifacts.
- `terminate()` — terminate the execution.
- `get_usage()` — collect token, time and cost information.

Adapters must remain stateless and independent of database operations.

### 6.3 Benchmark execution

Each agent must receive:

- The same task.
- The same base commit.
- Equivalent execution limits.
- Equivalent model budget.
- Equivalent tool permissions.
- The same acceptance criteria.

The generated patch must be evaluated in a fresh environment.

Agent identity must not influence Judge scoring.

### 6.4 Leaderboard

The public leaderboard must provide:

- Overall ranking.
- Task success rate.
- Regression-free acceptance rate.
- Cost per accepted fix.
- Tokens per accepted fix.
- Median repair time.
- Performance by programming language.
- Performance by task category.
- Performance by difficulty.

Leaderboard entries must disclose sample size, evaluation configuration and statistical uncertainty.

## 7. GitHub Review Requirements

### 7.1 GitHub App

The application must support:

- GitHub App installation.
- Repository selection.
- Pull Request webhooks.
- Diff retrieval.
- Commit tracking.
- Re-evaluation after new commits.
- GitHub Check Runs.
- Inline review comments.

### 7.2 Review workflow

- GitHub sends a Pull Request event.
- FixJury retrieves the PR metadata and diff.
- A clean repository snapshot is prepared.
- Automated verification is executed.
- Evaluation Core collects the results.
- AI Judge evaluates the patch.
- FixJury generates a structured report.
- Results are published to GitHub.

### 7.3 Quality Gates

Repository owners must be able to configure:

- Minimum quality score.
- Mandatory test suites.
- Security thresholds.
- Regression policies.
- Allowed file paths.
- Maximum change size.
- Judge evaluation mode.
- Evaluation budget.

Quality Gates must distinguish between:

- PASS
- FAIL
- INCONCLUSIVE

AI-generated findings must not be treated as confirmed security vulnerabilities without sufficient supporting evidence.

### 7.4 Pull Request report

The report must include:

- Quality score.
- Final verdict.
- Automated test results.
- Security findings.
- Regression findings.
- Code quality findings.
- Judge explanation.
- Execution time.
- Evaluation cost.

FixJury must never automatically merge a Pull Request.

## 8. Data Model

The system must support the following logical entities:

- organizations
- repositories
- github_installations
- pull_requests
- agents
- agent_versions
- agent_runs
- benchmark_tasks
- benchmark_experiments
- evaluation_runs
- evaluation_results
- judge_configurations
- judge_assessments
- judge_escalations
- evaluation_findings
- quality_policies
- usage_records
- metric_snapshots

All evaluation results must reference:

- Source module.
- Task or Pull Request.
- Agent run, when applicable.
- Evaluation configuration.
- Judge configuration.
- Execution artifacts.
- Timestamp.

Database schema changes must use Alembic migrations.

## 9. API Requirements

The platform must expose APIs for:

- Agent registration.
- Benchmark task retrieval.
- Benchmark execution.
- Pull Request evaluation.
- Judge configuration.
- Evaluation retrieval.
- Evaluation history.
- Metrics and analytics.
- Leaderboard retrieval.

All API responses must use validated Pydantic schemas.

Operations that may take significant time must run asynchronously through the existing Redis queue and worker infrastructure.

API authentication and organization-level authorization are required for private resources.

Exact endpoint paths and request schemas will be defined during the planning phase.

## 10. Security Requirements

Security is a mandatory platform requirement.

- Every agent execution must use an isolated environment.
- Untrusted repository code must never run in the platform process.
- Verification must happen in a fresh container.
- Network access must be disabled by default.
- External network access must use explicit allowlists.
- Provider credentials must never be exposed to unrelated processes.
- Hidden tests must never be available to coding agents or Judge.
- GitHub webhook signatures must be verified.
- Organization resources must be isolated.
- Secrets must never be stored in source code or logs.
- Judge output must be treated as untrusted input.
- Repository instructions and source code must be treated as potentially adversarial prompt injection.
- All execution environments must have CPU, memory and time limits.

## 11. Tech Stack

Preserve the existing stack unless a documented technical limitation requires a change.

| Component             | Technology           |
| --------------------- | -------------------- |
| Backend               | Python 3.12          |
| API                   | FastAPI              |
| Database              | PostgreSQL           |
| ORM                   | SQLAlchemy 2         |
| Migrations            | Alembic              |
| Queue                 | Redis                |
| Execution             | Docker               |
| Schemas               | Pydantic V1.1          |
| CLI                   | Typer                |
| Observability         | OpenTelemetry        |
| Frontend              | Next.js / TypeScript |
| Charts                | Chart.js             |
| Dependency management | uv                   |

AI Judge providers must be integrated through a provider abstraction rather than hardcoded into evaluation logic.

New dependencies require justification and approval.

## 12. Commands

Preserve existing CLI commands and behavior.

Existing commands:

```bash
uv sync
uv run fixjury --help
uv run pytest -q
uv run ruff check .
uv run mypy src
docker compose up -d
uv run alembic upgrade head
uv run fixjury bench --agents baseline,mini --verify
uv run fixjury verify --run <id>
uv run fixjury report --charts
uv run fixjury dashboard
```

Proposed new command capabilities:

```bash
fixjury judge evaluate --run <id>
fixjury judge validate --config <path>
fixjury review github --pr <url>
fixjury review policy --repo <name>
fixjury leaderboard
```

Exact command syntax must be validated against the existing CLI architecture during implementation planning.

## 13. Project Structure

Preserve existing modules and extend them incrementally.

Proposed logical structure:

```text
src/fixjury/
├── agents/
│   ├── adapters/
│   └── sdk/
├── judge/
│   ├── providers/
│   ├── prompts/
│   ├── schemas/
│   ├── evaluator.py
│   ├── scoring.py
│   └── escalation.py
├── evaluation/
│   ├── tests/
│   ├── regression/
│   ├── security/
│   └── static_analysis/
├── benchmark/
├── review/
│   └── github/
├── metrics/
├── leaderboard/
├── analytics/
├── api/
└── ...

benchmarks/
tests/
web/
docs/
migrations/
```

This is a target logical organization, not an instruction to move existing files unnecessarily.

## 14. Code Style

Follow the existing AGENTS.md conventions.

- Python 3.12.
- Full type hints.
- Strict mypy.
- Ruff formatting and linting.
- Async I/O.
- Pydantic validation.
- Functions and modules preferred over stateful service classes.
- Prompts stored as Markdown files.
- Configuration managed through Pydantic Settings.
- Database changes through Alembic.
- Conventional commits.

Example evaluation result:

```python
class JudgeVerdict(BaseModel):
    verdict: Literal[
        "ACCEPTED",
        "REJECTED",
        "INCONCLUSIVE",
    ]
    overall_score: float
    confidence: float
    findings: list[EvaluationFinding]
    reasoning_summary: str
    escalation_required: bool
```

All numeric values must be validated against their permitted ranges.

## 15. Testing Strategy

### Unit tests

- Judge scoring.
- Confidence handling.
- Escalation policies.
- Verdict aggregation.
- Evaluation configuration.
- Cost calculation.
- Metrics.
- API schema validation.

### Integration tests

- Judge provider integration.
- Redis queue.
- Docker sandbox.
- Verification pipeline.
- GitHub webhook processing.
- Database persistence.
- Evaluation artifact storage.

### End-to-end tests

- Complete benchmark execution.
- Single Judge evaluation.
- Adaptive escalation.
- Multi-Judge evaluation.
- GitHub Pull Request evaluation.
- Quality Gate behavior.
- INCONCLUSIVE handling.
- Budget exhaustion.
- Provider failure and fallback.

### Required verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
```

Changes to the frontend must also pass its existing typecheck and build workflow.

## 16. Boundaries

### Always

- Preserve existing functionality unless explicitly replaced.
- Use the shared Evaluation Core.
- Record all Judge assessments.
- Validate AI responses.
- Keep evaluation reproducible.
- Use isolated execution environments.
- Run tests before committing.
- Record excluded runs and their reasons.
- Keep benchmark tasks immutable after execution begins.
- Make evaluation costs observable.

### Ask first

- Database schema changes.
- New external dependencies.
- Changes to benchmark metrics.
- Changes to Judge scoring criteria.
- Changes to sandbox security limits.
- Changes to published benchmark results.
- Changes to existing public APIs.
- Changes to the existing execution architecture.

### Never

- Expose hidden tests to agents or Judge.
- Trust an AI-generated verdict without validation.
- Allow Judge to modify evaluated code.
- Execute untrusted code in the platform process.
- Automatically merge Pull Requests.
- Store credentials in source code.
- Silently change evaluation formulas.
- Mark an inconclusive evaluation as accepted.
- Present AI confidence as a statistically calibrated probability without validation.

## 17. Success Criteria

The V1.1 release is considered complete when:

### Evaluation Core

- A single Judge can complete an evaluation independently.
- Adaptive mode can invoke additional Judges.
- Multi mode supports independent evaluations.
- Judge configuration is adjustable.
- Budget and escalation limits are enforced.
- All verdicts use a validated schema.
- INCONCLUSIVE is supported.
- Evaluation results are reproducible within documented provider limitations.

### Benchmark

- Existing benchmark workflow remains functional.
- At least three agent configurations can be compared.
- Benchmark tasks are executed under equivalent conditions.
- Leaderboard metrics are generated from persisted evaluation results.
- Reports include sample size and evaluation configuration.

### GitHub Review

- GitHub App can receive Pull Request events.
- Pull Request diffs can be evaluated.
- Automated checks run in isolated environments.
- AI Judge produces structured results.
- Reports are published to GitHub.
- Quality Gates are configurable.
- No automatic merge occurs.

### Security

- Untrusted code is isolated.
- Hidden tests are not exposed.
- Credentials are protected.
- Webhook signatures are verified.
- Budget and resource limits are enforced.

### Quality

- Existing test suite passes.
- New unit and integration tests pass.
- Existing CLI functionality remains compatible.
- Documentation is updated.

## 18. Out of Scope (Deferred)

The following features are intentionally excluded from V1.1:

- Automatic Pull Request merging — FixJury evaluates code but does not control repository merging.
- Automatic code repair — FixJury evaluates patches rather than generating them.
- Model fine-tuning — evaluation data may be collected, but training is a separate initiative.
- Automatic self-improving prompts — requires a separate safety and experimentation design.
- Enterprise SSO — deferred until multi-tenant product requirements are finalized.
- Full multi-language support beyond the initial selected languages.
- Mandatory human review — the evaluation workflow is AI-only.
- Automatic claims of long-term reliability — deferred until sufficient post-merge telemetry is available.

## 19. Open Questions

These decisions must be finalized before implementation planning:

- Which Judge provider should be supported first?
- Should Judge use a separate provider from the coding agent by default?
- Should AI Judge be allowed to access the target repository's full source code or only relevant files?
- Should organizations be able to bring their own model API keys?
- Should the initial GitHub integration support private repositories?
- Which benchmark metrics should be included in the first public leaderboard?
- What are the default evaluation budgets and confidence thresholds?
- Should the public benchmark use a fixed Judge model version for all comparisons?
- What level of Judge calibration is required before publishing official rankings?

## 20. Specification Approval

This document defines the proposed product scope and requirements for FixJury V1.1.

Implementation must not begin until the specification has been reviewed and approved.

After approval, the next phases are:

- Create the technical implementation plan.
- Break the plan into dependency-ordered tasks.
- Define acceptance criteria and verification steps for every task.
- Implement incrementally, preserving existing functionality.

The specification is the source of truth for V1.1. New features outside this scope must be recorded as deferred work or handled through a separate specification.

End of specification.
