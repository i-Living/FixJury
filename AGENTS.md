# AGENTS.md

Engineering rules for FixJury. Read this before changing anything in the repo. Humans and agents follow the same rules.

## What this is

FixJury measures whether autonomous coding agents actually fix defects: it runs several agents on identical frozen repair tasks, verifies their patches in isolated containers, collects blind human verdicts, and reports acceptance, regressions, time and cost per accepted fix.

**Current stage: spec only.** No toolchain, no schema, no code. The authoritative design is [`docs/PLAN.md`](docs/PLAN.md); the public summary is [`README.md`](README.md). If this file disagrees with `docs/PLAN.md`, `docs/PLAN.md` wins and this file gets fixed in the same change.

## Stack

- Python 3.12, `uv` for env and dependencies.
- FastAPI + SQLAlchemy 2.0 (async) + Alembic, PostgreSQL 16.
- Redis 7 as the run queue, one worker process type.
- Docker (docker-py) for sandboxes: one container per run, no network by default.
- `typer` for the CLI, `pydantic` v2 for schemas, `pydantic-settings` for config.
- OpenTelemetry for traces, `matplotlib` for report charts.
- Next.js (TypeScript), static export only, for the read-only metrics page served by nginx on the VPS.

## Commands

All commands assume the repo root. The toolchain lands in task 0.1 of the plan; until then these are targets, not working commands.

```bash
uv sync                                   # install
uv run fixjury --help                     # CLI
uv run pytest -q                          # all tests
uv run pytest -q -m "not integration"     # fast tests
uv run ruff check .                       # lint
uv run ruff format .                      # format
uv run mypy src                           # types
docker compose up -d                      # postgres + redis
docker build -t fixjury-mini:2.4.6 dockerfiles/mini   # the mini harness image
# then set FIXJURY_MINI_IMAGE=fixjury-mini:2.4.6 and FIXJURY_MINI_VERSION=2.4.6 in .env
uv run alembic upgrade head               # migrations
uv run fixjury tasks validate benchmarks  # corpus check
uv run fixjury run --task T-001 --agent baseline
uv run fixjury verify --run <id>          # fresh container, policy, held-out
uv run fixjury bench --agents baseline,mini --verify
uv run fixjury review pack && uv run fixjury review serve
uv run fixjury metrics compute --agent baseline
(cd web && npm ci)                        # once, Node 20.9 or newer
uv run fixjury report --charts && uv run fixjury dashboard
```

`fixjury dashboard` shells out to `npm run build` in `web/` and copies the static export to `--out`, so
Node 20.9 or newer and a `npm ci` in `web/` are prerequisites for that one command. A bare `npm run build`
is not a shortcut: the page is prerendered from the snapshot export, so it fails outside that command. CI
does not build the page: the Python check chain below is the merge gate.

Definition of done for any code change:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

A change that touches `web/` also runs `(cd web && npm run typecheck)`, and the full static build through
`uv run fixjury dashboard --out <dir>`, which is the only thing that supplies the snapshot export.

## Layout

```
src/fixjury/config.py      settings from env, pricing version
src/fixjury/db.py          engine and session
src/fixjury/models.py      SQLAlchemy models (tasks, runs, verdicts, rejections, batches, snapshots)
src/fixjury/cli.py         typer entry point
src/fixjury/queue.py       Redis lists: enqueue, claim, resume
src/fixjury/worker.py      one run per claim, retry as a new attempt
src/fixjury/bench.py       plan, enqueue and drain a task x agent grid
src/fixjury/run.py         run one agent on one task, record it in runs
src/fixjury/telemetry.py   OTel spans; the trace id is stored on every run
src/fixjury/tasks/         task.yaml schema, loader, freeze and validate
src/fixjury/spec/          RepairSpec generation and rendering
src/fixjury/agents/        AgentAdapter protocol + baseline + mini + container harness
src/fixjury/sandbox/       container lifecycle, per-run network, tool API, diff capture, egress proxy
src/fixjury/verify/        check runner, held-out tests, policy, verdict assembly
src/fixjury/review/        blind batching, rubric, review page (app.py + templates/)
src/fixjury/metrics/       metric definitions (compute.py), snapshots (service.py), cost
src/fixjury/report/        markdown report, charts, the export the metrics page is built from
src/fixjury/prompts/       *.md prompt files (English)
config/pricing.yaml        pinned rates + pricing_version
dockerfiles/mini/          the mini-swe-agent harness image (one pinned Dockerfile)
benchmarks/tasks/T-xxx/    task.yaml, public fixtures
benchmarks/heldout/T-xxx/  held-out tests + reference.patch, mounted only in verification
web/                       read-only metrics page: Next.js app, static export, charts via Chart.js
tests/unit, tests/integration
docs/PLAN.md               spec + plan
docs/plans/                one plan + checkpoint per phase
```

## Invariants

These are load-bearing. Breaking one silently invalidates published numbers.

1. **The diff is the only transport.** An agent produces a unified diff; verification applies it to a pristine checkout of `base_commit`. Never verify inside the agent's container, never trust the agent's workspace state.
2. **Held-out tests never reach the agent.** They live in `benchmarks/heldout/<id>/` in this repository — public by design — and isolation comes from the setup, not from secrecy: an agent only ever receives a checkout of the *target* repository at `base_commit`, with no network, so `benchmarks/` is not in its container. Mount held-out tests read-only into the verification container only.
3. **Verification is a fresh container** built from `repo@base_commit` + diff, with the tool versions pinned in the task spec.
4. **Review is blind.** The reviewer sees task, spec, patch and check results; agent and model fields are stripped. Blindness is auditable through `review_batches`, so shuffle order is stored, not regenerated.
5. **Metrics are computed once**, in `src/fixjury/metrics/`, from `runs` + `verdicts` + `rejections`, and persisted to `metric_snapshots` with `config_hash` and `pricing_version`. The report and the UI read snapshots. No metric arithmetic in SQL strings, notebooks, the dashboard or a human's head.
6. **Adapters are stateless.** `AgentAdapter` may not write to the database, read global config or share objects between runs. Everything it needs arrives in the run spec.
7. **Sandbox has no network and no docker socket.** The baseline agent calls the model from the platform process while tools execute inside the container. Third-party agents run inside the container on a per-run Docker network created with `internal=True`, so the container has no route out at all; the egress proxy binds the gateway of that network and allowlists one LLM endpoint. `HTTP_PROXY` is how the harness finds the proxy, never what keeps it in: a command that unsets the variable reaches nothing. The provider key does stay in the sandbox — the endpoint is HTTPS, and injecting the header at the proxy would need TLS interception — so the credential boundary is the network: there is nowhere to send the key except the allowlisted endpoint.
8. **Cost has two bases, and both are labelled.** Models run on a flat opencode-go subscription, so there is no per-run invoice: report `list_price` (tokens × pinned price + sandbox seconds × pinned rate) as a reference and `amortized` (share of the subscription over the run window) as actual money. `tokens_per_accepted_fix` is the primary comparable metric. Prices live in config with a `pricing_version`; costs across different versions are not comparable and must be labelled.
9. **Frozen means frozen.** Editing `benchmarks/tasks/*/task.yaml` after any run exists invalidates the comparison. Add a new task version instead.
10. **No auto-merge.** FixJury never pushes to a target repo, never opens a PR against a third-party repository.

## Conventions

- Functions and modules over classes. Classes are allowed only for SQLAlchemy models, pydantic models, the `AgentAdapter` protocol and its adapters. No inheritance beyond protocols, no service-layer objects with hidden state.
- Full type hints on public functions, no `Any` without a comment.
- Async for I/O. The docker SDK is synchronous: wrap it with `asyncio.to_thread`.
- One vocabulary everywhere: `task`, `spec`, `run`, `attempt`, `verdict`, `rejection`, `batch`, `snapshot`. Do not introduce `job`, `result`, `review_result` as synonyms in code, DB, API or UI.
- Prompts are English and live in `src/fixjury/prompts/*.md`. No prompt strings inside Python.
- Config through `pydantic-settings`, never a hardcoded path or a secret in the repo.
- Schema changes go through Alembic. `create_all` is allowed in tests only.
- Unit tests for pure logic (policy, metrics, cost, taxonomy); integration tests are marked `@pytest.mark.integration` and may require docker.
- Conventional commits, one plan task per commit, `--no-verify` never.

## Boundaries

**Always**
- Run the check chain before committing.
- Update `docs/PLAN.md` when a decision changes, and `AGENTS.md` when the stack, commands or workflow change.
- Record an exclusion reason for every run that is dropped from the metrics (`infra_failed`, `timeout`, `flaky_task`).

**Ask first**
- Adding a dependency.
- Changing the DB schema.
- Changing a metric definition or a rubric rule (it invalidates previously published numbers).
- Changing sandbox limits or timeouts.
- Publishing benchmark results or deploying the demo.

**Never**
- Commit API keys, tokens or `.env`.
- Put hidden tests or hidden fixtures in this repository — they are not hidden, and pretending otherwise is worse than saying they are public.
- Mount this repository, or anything under `benchmarks/`, into an agent container.
- Let an agent, an adapter or the UI write metrics or verdicts on the agent's behalf.
- Push to a target repository or open a PR there.
- Compare costs computed with different `pricing_version` values without saying so.

## Pitfalls

- **Untracked files vanish from a diff.** Capture with `git add -N . && git diff --binary --no-color`; reject binary diffs with an explicit `UNREADABLE_DIFF` rejection instead of dropping them silently.
- **`base_commit` must be the merge-base**, not the issue date or the branch head, otherwise the diff contains unrelated commits.
- **Per-tool-call timeout and per-run timeout are different things.** The run cap must kill the container, not only the Python process.
- **Enum types outlive their tables.** `alembic downgrade` drops tables but leaves PostgreSQL enum types behind, so the next `upgrade` fails with `type ... already exists`. Drop the types explicitly at the end of `downgrade()`.
- **Containers leak.** `finally: container.remove(force=True)`, and the bench runner reaps leftover containers at start.
- **opencode-go needs `x-opencode-session`.** The Zen Go endpoint rejects requests without it; every adapter that talks to the provider must send the header, otherwise runs fail as `infra_failed` for no visible reason.
- **Never mount the platform repo into a sandbox.** The agent gets `target_repo@base_commit` only; mounting FixJury would hand it `benchmarks/heldout` and quietly destroy the benchmark.
- **The run network gateway is a host address.** `internal=True` blocks routes out, not the gateway: a platform service bound to `0.0.0.0` would be reachable from the sandbox. The egress proxy binds that gateway address and every other platform service belongs on loopback. Docker Engine must be 26.0.0 or newer (25.0.5, 23.0.11): older engines forwarded DNS from internal networks to a host resolver, which a container could encode data in.
- **Proxy variables are not isolation.** `HTTP_PROXY` inside a container is a suggestion: on a bridge network a command unsets it and dials out. The allowlist only holds if the container has no other route — a per-run `internal=True` network whose only reachable peer is the gateway where the proxy listens. `NO_PROXY` must stay empty: an exception for localhost silently bypasses the proxy for a self-hosted endpoint.
- **Check commands run in the repo root** of the task; monorepos and pytest config resolution break silently otherwise.
- **Flaky tasks poison acceptance rates.** Run a no-op baseline (empty diff) against the base commit before the benchmark and drop tasks that fail their own tests.
- **Never name a top-level directory after a package you import.** A `docker/` folder at the repo root
  made mypy resolve `import docker` to the Dockerfile directory and report the SDK as missing methods.
- **mini-swe-agent interviews you on first run.** Without `MSWEA_CONFIGURED=1` it tries to read a
  model and a key from stdin, which a sandbox does not have, and the run dies as `infra_failed`. The
  harness sets it, along with `HOME=/tmp`: the root filesystem is read-only and mini writes a last-run
  copy under `$HOME`.
- **A step limit is not a failed exit.** mini exits 0 when it stops at `step_limit`; the declaration of
  success is the `exit_status` in its trajectory (`Submitted`), not the exit code. Reading only the exit
  code would record every truncated run as a false completion.
- **Harness tokens come from the trajectory, not from the platform.** A container harness that records
  no trajectory reports zero tokens, which makes `tokens_per_accepted_fix` a lie rather than a gap.
- **Tool versions change verdicts.** Pin ruff/mypy/pytest per task and store `tool_versions` on the verdict; an unpinned upgrade silently rewrites history.
- **Blind review leaks through metadata.** Patch metadata, commit messages, code comments and PR-style formatting can reveal the agent, so packaging strips names and normalizes headers before review.
- **A zero denominator is not zero cost.** `cost_per_accepted_fix` is `null`, not `0`, when nothing was accepted; the report must show `null`.

## Review bot

CodeRabbit reviews every pull request. Its configuration is [`.coderabbit.yaml`](.coderabbit.yaml), validated against the published schema (`https://coderabbit.ai/integrations/schema.v2.json`): an unknown key makes CodeRabbit drop the whole file silently and review with defaults instead.

- It is advisory. `auto_approvals` is off and none of its checks is required to merge; a human merges.
- It covers what the check chain cannot see: the invariants above (isolation, blind review, metrics computed once) and the "ask first" list.
- Its findings are triaged like a human review — fix them, or say in the pull request why not. Never apply a suggestion that adds a dependency, changes the schema, a metric definition, the rubric or sandbox limits.
- Review comments are English, like the code and the prompts.

## Verification

Before declaring a phase complete, run the check chain and the phase verification listed in `docs/PLAN.md` §11. The MVP is done when `docs/PLAN.md` §14 is satisfied: frozen corpus, 96 completed pairs, 96 human verdicts, computed metrics, published report, green CI.
