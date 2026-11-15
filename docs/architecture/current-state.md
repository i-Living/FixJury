# FixJury V1.1 — Current State

**Audited at:** `c5fc958` on `master`, 15 November 2026.
**Scope:** documentation only — no implementation change was made in this phase (PLAN_V1.1 §2).
**Authority:** [`docs/PLAN.md`](../PLAN.md) defines V1. Where this document and the code disagree, the code
is right and this document gets fixed. Where V1.1 needs something V1 does not have, the gap is listed in
[§13](#13-technical-debt-and-conflicts-with-v11) and mapped in [`migration-map.md`](migration-map.md).

## 1. Runtime surfaces

| Surface | Started by | Process | Writes |
| --- | --- | --- | --- |
| CLI | `fixjury <command>` (`src/fixjury/cli.py`) | platform process | tasks, runs, verdicts, rejections, batches, snapshots |
| Worker | `fixjury worker` (`src/fixjury/worker.py`) | one process, one run per claim | runs, verdicts, rejections |
| Bench runner | `fixjury bench` (`src/fixjury/bench.py`) | platform process, drives the queue in-process | runs, verdicts via `verify_run` |
| Read-only API | `fixjury api` (`src/fixjury/api/app.py`) | FastAPI | nothing |
| Blind review page | `fixjury review serve` (`src/fixjury/review/app.py`) | FastAPI + Jinja templates | reviewer/human verdict columns |

There is exactly one worker process type (AGENTS.md invariant 7). Every long operation is either driven
inline by the CLI or claimed from Redis by the worker; nothing else owns a queue.

## 2. Execution lifecycle (agent run)

`fixjury run --task T-001 --agent baseline` → `run.run_task` (`src/fixjury/run.py`):

1. `build_adapter(agent)` returns a stateless adapter from `agents/registry.py`.
2. `checkout_workspace(repo_url, base_commit, workspace)` materialises `repo@base_commit` in a temp
   directory, reusing a local mirror under the configured checkout cache; the network is used only to
   populate that mirror.
3. `RunRequest` carries everything the adapter may read: `run_id`, `repo_url`, `base_commit`, rendered
   `RepairSpec` text, workspace path, image, `max_steps`, `timeout_s` and an `LlmConfig` (base URL, model,
   key, session header).
4. The adapter runs inside `open_sandbox` (`sandbox/container.py`): read-only root, tmpfs `/tmp`, dropped
   capabilities, `no-new-privileges`, CPU/memory/pids limits, `network=none` unless the adapter asks for a
   per-run network.
5. The adapter returns `AgentResult`: unified diff, declared success, unable reason, tokens, steps, sandbox
   seconds, summary. The diff is the only transport out of the container.
6. `run_task` writes the `runs` row: status (`finished` / `infra_failed`), diff + diff sha256, tokens,
   sandbox seconds, list-price cost, `pricing_version`, `config_hash`, trace id, timestamps.

Verification never reads the agent's workspace; it re-applies the diff to a pristine checkout (§3).

## 3. Verification lifecycle

`fixjury verify --run <id>` → `verify.service.verify_run` → `verify.runner.verify_workspace`:

1. **Policy first, on the diff text only** (`verify/policy.py`): changed-file parsing, allowed/forbidden
   paths, protected paths, test-tree rules, maximum changed files. Violations become `rejections` with a
   `RejectionCode`.
2. **Checkout the base commit** into a fresh temp directory, then `apply_patch`; a diff that does not apply
   is a `BUILD_BREAK` result, not an error.
3. **Install** (`INSTALL_STEPS`) inside a container on a per-run `internal=True` network whose only
   reachable peer is an `AllowlistProxy` bound to the network gateway, allowlisting the package index. A
   failed install is retried once; if it still fails, the same install runs on a separate pristine
   checkout of the base commit. Base fails too → the environment is unusable and the pair is an exclusion
   with a recorded reason. Base succeeds → the diff broke a build that worked, which is a failed fix.
4. **Checks** from `task.yaml`: only `pytest`, `ruff`, `mypy` are whitelisted (`tasks/schema.py::CheckTool`;
   flags, shell syntax and absolute paths are refused). They run with `network=none` in the same container,
   each with its own timeout.
5. **Tool versions** are probed in the container and stored on the verdict (`tool_versions`) so an unpinned
   tool cannot silently rewrite history.
6. **Held-out tests** are overlaid *after* the task's own checks, so `tests_pass` measures the agent's tree
   and `heldout_pass` measures the post-fix tests. The bundle lives in `benchmarks/heldout/<id>/` and only
   ever reaches this container.
7. `verify.verdict.assemble` collapses outcomes into `tests_pass` / `lint_pass` / `type_pass` /
   `policy_pass` / `heldout_pass` / `automated_pass` plus rejections; `verify.service.store_verdict` replaces
   the verdict row and rewrites its rejections in one transaction.
8. A verification environment that cannot be built is not a run result: the run is marked `infra_failed`
   with a bounded reason and the pair leaves the metrics with that exclusion reason.

`CheckOutcome` retains the command, exit code and a 2000-character output tail during verification. Only the
booleans and the rejections survive into the database — the evidence itself is not persisted.

## 4. Result format today

| Row | Table | Shape |
| --- | --- | --- |
| Task | `tasks` | frozen corpus document: repo, license, base/fix commits, issue, difficulty, `spec` JSONB, cutoff assumption, held-out ref, frozen-at/by |
| Run | `runs` | one agent × one task × one attempt: adapter name + version, model, status, diff + sha, tokens, sandbox seconds, cost, `pricing_version`, `config_hash`, trace |
| Verdict | `verdicts` (PK = `run_id`) | five booleans, `tool_versions`, `reviewer_*`, `human_*`, `human_resolution`, `blind`, `batch_id`, `reviewed_at` |
| Rejection | `rejections` | `source` (automated/reviewer/human), `code` (ten-value enum), `note` |
| Review batch | `review_batches` | `task_id`, `run_ids`, `order_seed`, `pass_no`, `reviewer` |
| Metric snapshot | `metric_snapshots` | `agent`, `cost_basis`, `config_hash`, `payload` JSONB |

The verdict is therefore a **checklist plus a human judgement**, one row per run. It has no per-criterion
score, no findings, no confidence and no judge identity.

## 5. Data model and relationships

Six tables and six PostgreSQL enum types (`difficulty`, `cutoff_assumption`, `cost_basis`, `run_status`,
`reviewer_verdict`, `human_verdict`, `human_resolution`, `rejection_source`, `rejection_code`):

- `tasks 1—N runs` (`runs.task_id` foreign key)
- `runs 1—1 verdicts` (`verdicts.run_id` is the primary key)
- `runs 1—N rejections`
- `tasks 1—N review_batches`
- `metric_snapshots` has **no foreign keys**: it is a derived artefact keyed by `agent`, `cost_basis` and
  `config_hash`; `pricing_version` travels inside the payload and in the run row.

Migrations live in `migrations/versions/` (initial schema + `record_unable_reason`) and are written so a
`downgrade()` drops the tables *and* the enum types.

## 6. Queue and worker

`queue.py` owns Redis lists: `pending`, `processing`, `done`. Enqueue persists the `runs` row **before**
pushing the id, so the row is the record and the queue is transport. `claim` moves an id to `processing`,
`complete` removes it, `resume` returns orphans to the queue. `worker.process_run` takes a `NOWAIT` row lock
for the whole attempt (a duplicate delivery or a second worker backs off instead of running it twice),
skips runs that already settled, and requeues an `infra_failed` run as a **new attempt** with
`parent_run_id`. The queue id is acknowledged only after the transaction that recorded the run commits.
`bench.drain` works the queue one transaction per run and verifies each finished run when `--verify` is set.

## 7. Sandbox and network

- `SandboxSpec` is the whole contract: image, workspace, workdir, CPU, memory, pids, tmpfs, timeout,
  network mode, run-as user. Adapters never read global settings.
- `open_sandbox` creates the container through `asyncio.to_thread` (the docker SDK is synchronous), starts a
  watchdog that **kills the container** at `timeout_s`, and removes it in `finally`. `reap_sandboxes` and
  `reap_networks` clean up leftovers at bench start.
- Container defaults: `read_only=True`, tmpfs `/tmp` (`noexec`, `nosuid`), workspace bind-mounted read-write
  at `/workspace`, `cap_drop=ALL`, `no-new-privileges`, memory/CPU/pids limits, `network=none`.
- Two narrower escapes exist and both are allowlisted: a harness container gets a per-run `internal=True`
  network whose only reachable peer is the egress proxy (`sandbox/proxy.py`), and the verification install
  container gets the same shape with the package index as the only allowed target. `HTTP_PROXY` is a route,
  never the permission; the internal network is what keeps the container in.
- Docker Engine must be 26.0.0 or newer (internal-network DNS forwarding).

## 8. Agents and adapters

`agents/base.py` defines `AgentAdapter` as a protocol with one operation:

```python
async def run(self, request: RunRequest) -> AgentResult
```

`name`, `version` and `model` are adapter attributes. Three adapters exist:

- `baseline.py` — the reference agent: the platform calls the model and executes tools in the sandbox.
- `mini.py` — mini-swe-agent inside the harness image; tokens are read from its trajectory, not estimated.
- `openhands.py` + `dockerfiles/openhands/openhands_driver.py` — OpenHands SDK in the harness image, with
  the driver mapping the SDK's step-limit status instead of reporting a crash.

`container_harness.py` holds the shared container-side plumbing (mounts, env, trajectory parsing), and
`trajectory.py` parses harness output. Adapters are stateless and never touch the database (AGENTS.md
invariant 6); everything they need arrives in `RunRequest`.

## 9. Configuration

`config.Settings` (pydantic-settings, `.env`): database and Redis URLs, log level, sandbox image/CPU/memory/
timeout/output limit, GitHub API URL and token, LLM base URL/model/key/session/timeout, harness image +
command + version for mini and OpenHands, checkout cache, pricing file and `pricing_version`.
Prices live in `config/pricing.yaml`; every cost carries the `pricing_version` that produced it (`list_price`
and `amortized` are separate bases, AGENTS.md invariant 8). `run.config_hash` captures the run's inputs;
`metrics.service.config_hash_of` captures the metric inputs.

## 10. Metrics, report, dashboard, export

- `metrics/compute.py` is pure: `Attempt` records in, `MetricsReport` out (pairs, reviewed pairs, accepted,
  regression-free accepted, acceptance rates with intervals, `tokens_per_accepted_fix`,
  `cost_per_accepted_fix`, median time to repair, false-completion rate).
- `metrics/service.py` reads `runs` + `verdicts` + `rejections`, computes once, and persists a
  `metric_snapshots` row with `config_hash` and `pricing_version` (AGENTS.md invariant 5).
- Acceptance divides by **reviewed** pairs only and is `null` until a verdict exists; a zero denominator
  yields `null`, never `0`.
- `report/markdown.py` and `report/charts.py` render from a snapshot; `report/dashboard.py` shells out to
  `npm run build` and copies the static export; `api/routes.py` serves runs/verdicts/snapshots read-only.
- `export.py` writes raw runs/verdicts/rejections as JSONL plus a hashed manifest.

## 11. Blind review

`review/pack.py` builds `BlindItem`s: agent and model fields are stripped, diff headers normalised, order
derived from a stored seed per `(task_id, pass_no, base_seed)` and persisted in `review_batches` — never
regenerated on read. `review/app.py` serves the batch and writes `reviewer_*` / `human_*` columns with
`blind=true`. Only a patch with `automated_pass=true` is eligible. The rubric comes from the task document.

## 12. Testing and gates

- `tests/unit` covers pure logic (policy, metrics, cost, taxonomy, diff, spec, report, queue helpers);
  `tests/integration` is marked `@pytest.mark.integration` and may need docker or postgres.
- CI (`.github/workflows/ci.yml`): `ruff check`, `ruff format --check`, `mypy src`, `pytest -q`,
  `fixjury tasks validate benchmarks`; a second job typechecks and builds the static page.
- Definition of done (AGENTS.md): that chain, plus `npm run typecheck` and `fixjury dashboard` for `web/`.

## 13. Technical debt and conflicts with V1.1

Each line is a real constraint discovered in the code, not a style preference. The action column is the
smallest safe change and is expanded in [`migration-map.md`](migration-map.md).

| # | Constraint today | Conflicts with | Smallest safe change |
| --- | --- | --- | --- |
| 1 | `verdicts` is one human-centric checklist row keyed by `run_id` | V1.1 needs machine verdicts with scores, findings, confidence | Add evaluation tables; keep `verdicts` as the automated/human record; never rewrite history |
| 2 | Nothing persists per-criterion scores, findings or judge identity | §3.1 evaluation core, §4.5 judge output | New `evaluation_*` / `judge_*` tables; criterion scores in `evaluation_results` |
| 3 | `CheckOutcome` evidence is discarded after verification | Judge must cite evidence (§3.2, §4.5) | Persist structured check outcomes as `evaluation_findings` / artifacts, not free text |
| 4 | No coverage measurement; `CheckTool` = pytest, ruff, mypy | §1.2 coverage analysis | Whitelist `coverage`; parse its report into a check result; task files stay unchanged |
| 5 | No security scanner; security can only be judged, not verified | §5 critical failures | Record security findings as judge findings with evidence and never as confirmed automated failures |
| 6 | `llm.py` is a single call helper with a provider-specific session header | §4.1 judge provider abstraction | Wrap it in a `JudgeProvider` protocol; keep the HTTP code in one place |
| 7 | Adapters expose only `run()` | §3.2 `prepare/execute/collect/terminate/get_usage` | Thin SDK wrapper over `run()`; adapters keep their shape |
| 8 | No organization/repository entities; single tenant | §3.4, §7, §8 | Defer with GitHub Review; make the evaluation row carry a polymorphic subject |
| 9 | `RejectionCode` is the repair rubric | §4.5 finding taxonomy | Separate `EvaluationFinding` taxonomy; do not overload `RejectionCode` |
| 10 | Snapshot payload has no verdict-source dimension | AI-first workflow (decision 1) | Add `verdict_source` to the snapshot payload and its config hash |
| 11 | API is read-only, unauthenticated, has no evaluation endpoints | §9 | Phase 6; behind the same routes module |
| 12 | CLI has no `judge` / `evaluation` / `leaderboard` commands | §12 proposed commands | Add subcommands; existing commands untouched |
| 13 | Prompts exist only for spec drafting (`src/fixjury/prompts/`) | §3.2 versioned judge prompts | `src/fixjury/judge/prompts/*.md` with a version constant; no strings in Python |
| 14 | `PLAN_V1.1.md` links `spec.md` (absent) and its phase-0 deliverables restate PLAN.md | documentation hygiene | Fixed in this phase: link to `SPEC_V1.1.md`, deliverables written as current-state with references |

No implementation was changed in this phase. The audit found no case where the existing V1 pipeline has to be
rewritten for V1.1 to proceed: evaluation, judge and scoring can be added beside `verify`/`metrics`, and the
only schema change to an existing table is none — the new tables are additive.
