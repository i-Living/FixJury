# Phase 2 — sandbox, tool API, diff capture, baseline agent

Plan for `docs/PLAN.md` §11 phase 2. Order differs from the numbering: the diff is the only
transport (invariant 1), so it lands first and every later step is tested against it.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| 2.4 | `src/fixjury/sandbox/diff.py` — capture with `git add -N . && git diff --binary --no-color`, sha256, binary rejection | untracked file appears in the diff; a binary patch raises `UnreadableDiff` |
| 2.1 | `src/fixjury/sandbox/container.py` — one container per run, no network, read-only rootfs, tmpfs, limits, hard deadline, `finally: remove(force=True)` | `true` runs; `curl` fails; nothing left in `docker ps` |
| 2.2 | `src/fixjury/sandbox/tools.py` — exec/read/write/list, argv only, output cap, path confinement | unit tests on quoting, truncation and `..` escape |
| 2.3 | `src/fixjury/agents/base.py` + `baseline.py` — model in the platform process, tools in the container, bounded steps, answer is a diff or `unable` | a mocked model run produces a diff with `diff_sha256` and token counts |

## Non-negotiables carried in from `AGENTS.md`

- The platform captures the diff from the workspace; a diff written by the model is never trusted.
- Verification never runs inside the agent's container.
- The agent container gets the target repository only — never this repository, never `benchmarks/`.
- Per-tool-call timeout and per-run timeout are different limits, and the run cap kills the
  container, not only the Python task.

## Design

### Diff capture

```python
capture_diff(workdir: Path) -> CapturedDiff   # diff, sha256, is_empty
```

Runs `git add -N .` first: without it untracked files never appear in `git diff`, which is the
documented way to lose an agent's new module. `--binary` makes binary content explicit instead of
silently dropping it; any `GIT binary patch` section raises `UnreadableDiff`, which the run records
as the `UNREADABLE_DIFF` rejection rather than pretending the run produced nothing.

### Container

One `docker-py` container per run, created through `asyncio.to_thread` because the SDK is
synchronous:

- `network_mode="none"`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`
- `read_only=True` rootfs, `/tmp` on tmpfs, the workspace bind-mounted read-write
- `mem_limit`, `nano_cpus`, `pids_limit` from settings
- a hard deadline that removes the container, and `finally: container.remove(force=True)`

The handle is a frozen dataclass; the lifecycle is an async context manager, not a class, so no
object holds hidden state.

### Tool API

`exec(handle, argv, timeout_s)`, `read_file`, `write_file`, `list_dir`. `exec` passes an argv list
to `container.exec_run`, never a string, so a path with a space or a quote cannot become a second
command. Output is truncated at `sandbox_output_limit` bytes with an explicit marker, because a
tool result that silently loses the failing assertion is worse than a short one. Every path is
resolved inside the workspace root and refused if it escapes.

### Baseline agent

`AgentAdapter` is a protocol: `async def run(request: RunRequest) -> AgentResult`. The baseline
keeps the model call in the platform process (invariant 7) and executes tools in the container,
loops until the model answers or `max_steps` is reached, then the platform captures the diff.

Prompts live in `src/fixjury/prompts/baseline_agent.md`; no prompt text in Python.

## Verification checkpoint

- `uv run pytest -q` green, including the new unit tests.
- `uv run pytest -q -m integration` green with a live docker daemon (sandbox lifecycle).
- `docker ps` has no container after the integration test, including after a timeout.
- A mocked-model run produces a non-empty diff, a `diff_sha256`, and token counters.
- No module in `src/fixjury/sandbox/` or `src/fixjury/agents/` writes to the database.

## Checkpoint — landed

All four steps are in, plus the run orchestration that turns them into a recorded run:

| Piece | Where | Evidence |
| --- | --- | --- |
| Diff capture | `src/fixjury/sandbox/diff.py` | 9 unit tests: untracked file present, binary patch refused, digest changes with content |
| Container | `src/fixjury/sandbox/container.py` | 5 integration tests: command runs, host sees the write, egress fails, container gone, run cap kills |
| Tool API | `src/fixjury/sandbox/tools.py` | 18 unit tests: argv untouched, output capped, `../etc/passwd` refused |
| Baseline agent | `src/fixjury/agents/` | 13 unit + 2 integration tests: scripted plan → diff, same plan → same digest |
| Run orchestration | `src/fixjury/run.py`, `src/fixjury/db.py`, `fixjury run` | 12 unit + 4 integration tests against live Postgres, work rolled back |
| Registry | `src/fixjury/agents/registry.py` | unknown agent names the ones it knows |

Measured, not asserted:

- `fixjury run --task T-001 --agent baseline` against a local scripted endpoint cloned
  `Delgan/loguru` at `36da8cc`, ran the loop in a container and stored
  `sha256=de79ff58…` with `tokens=400/80`, `sandbox_seconds=0.40`. A second run of the same plan
  produced the same digest.
- With no reachable model the same command stores an `infra_failed` row instead of raising:
  `status=infra_failed tokens=0/0 diff=-`.
- `runs` rows carry `config_hash` (task, base commit, agent, agent version, model, image, timeout,
  max steps, pricing version) and `pricing_version="unpriced"`, so a later priced run cannot be
  silently compared with these.
- `sandbox_seconds` is the container's lifetime, not the loop's: the adapter measures around
  `open_sandbox`, because that is what a run is billed for.

Not done, and deliberately left to later phases: no verification (phase 3), no queue or retries
(phase 4), no third-party adapters, and no live model run — this environment has no provider key,
so the baseline is proven against a scripted endpoint and a scripted in-process model.
