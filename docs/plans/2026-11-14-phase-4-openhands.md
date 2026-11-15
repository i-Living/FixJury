# Phase 4.2 — the OpenHands harness

`docs/PLAN.md` §11 4.2 asks for OpenHands as the second container agent, and §14 needs three agents for
the 96-pair grid. Until now `openhands_image` was empty: the adapter existed as configuration and the
third executor had never run. This change adds the image, the entry point and the trajectory reader, so
`fixjury run --task <id> --agent openhands` is a real run.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| Entry point | `dockerfiles/openhands/openhands_driver.py` — the SDK agent the CLI builds, run once on `TASK.md` | smoke run against a mock OpenAI endpoint under the sandbox's own limits: 1 model call, tokens reported, status `finished` |
| Image | `dockerfiles/openhands/Dockerfile` — `openhands-ai==1.11.0` plus the ACP pin it needs | image builds; `import openhands.sdk` succeeds; the container reaches the endpoint |
| Harness | `src/fixjury/agents/openhands.py` — config writer, trajectory reader, command, env | unit tests: config, model naming, trajectory, outcome, registry wiring |
| Shared code | `src/fixjury/agents/trajectory.py` + `model_names.py` | mini's tests stay green against the shared reader |
| Wiring | `HarnessSpec.read_trajectory`, outcome notes, registry entry, `.env.example`, `AGENTS.md`, `docs/USAGE.md` | check chain green |

## Design

**Why a driver, not a flag.** OpenHands 1.x has no headless mode. The `openhands` console script was the
terminal UI and the GUI launcher and is gone from `openhands-ai` 1.11.0 entirely; the maintained surfaces
are the SDK, a terminal UI and a web app. A sandbox has no terminal, so the image carries this
repository's entry point on the SDK. It builds the same agent the CLI would — default tools minus the
browser — and runs it once.

**The browser tool is not a preference.** The default tool set includes a browser tool that refuses to
start without Chromium, and it refuses before the agent reads the task: the first attempt here died with
`Chromium is required for browser operations but is not installed`. The driver asks for
`get_default_tools(enable_browser=False)`. A sandbox with no route out has nothing to browse.

**A second pin, because the first one resolves a broken dependency set.** `openhands-ai` 1.11.0 accepts
`agent-client-protocol` 1.0.0, whose package no longer ships `acp.helpers`; `openhands-sdk` imports it at
start-up, so `import openhands.sdk` raises `ModuleNotFoundError` on an install pip calls successful. The
Dockerfile pins `agent-client-protocol==0.10.1` beside it.

**Tokens come from the harness, not from the platform.** The driver writes `fixjury-trajectory.json` with
the prompt and completion token sums from the SDK's own metrics, the number of model calls, and the
conversation's final status. The sandbox reports those numbers instead of zero (invariant 8). A run that
stops at the step ceiling ends `paused`, which the adapter records as "stopped with status paused" — not
as a completion.

**A conversation that ends in `error` explains itself.** The SDK keeps the final status, not a message:
`status error` alone left the first real run with an accepted patch and no visible cause. The driver
collects the last three failed tool observations through the SDK's own event callback and writes them
into the trajectory; the adapter appends that note to `unable_reason`. Without it the reason column is
silent, and a silent reason is what turns a measurable agent into an anecdote.

**A spent step ceiling is not a crash, and the SDK disagrees.** `LocalConversation.run()` sets
`execution_status = ERROR` with the code `MaxIterationsReached` when the iteration limit is reached
without a `finished` declaration — the same shape as a provider dying. The driver maps that code (and
`MaxBudgetReached`) to its own `step_limit` status and keeps the SDK code in the notes, so a run that
used every step it was given reads as "harness stopped with status step_limit: MaxIterationsReached: …"
rather than "error". With 30 steps, OpenHands spent them: the first two attempts on `T-001` finished
their edit and were cut off before declaring success.

**Both boundary files are stashed like mini's.** The config carries the key and the session header, so it
is written `0600`, removed before the diff is captured, and the captured patch is checked for the key
(`KEY_IN_DIFF`) the same way. A repository that already tracks either filename is restored, not
overwritten.

**Isolation is unchanged.** The agent runs on its own `internal=True` network whose only reachable peer is
the egress proxy bound to the gateway, and the proxy allowlists the model endpoint. The session header
travels inside the harness's own LLM call (the SDK's `extra_headers`), which was verified against a local
endpoint that logs request headers — the platform does not inject it and needs no TLS interception.

## Verification checkpoint

- `uv run ruff check . && uv run ruff format --check . && uv run mypy src` clean; `pytest -q -m "not
  integration"` — 396 passed.
- Smoke run of the driver against a local OpenAI-compatible endpoint, inside the sandbox's own limits
  (read-only root filesystem, `noexec` tmpfs at `/tmp`, 2 GB, dropped capabilities): the request carried
  `x-opencode-session`, the trajectory reported 11/7 tokens and `finished`.
- First real pair, `fixjury run --task T-001 --agent openhands`: `status=finished`,
  `agent_version=1.11.0`, 368 s of sandbox, tokens 477 254 / 11 865, diff 2033 B — the patch replaces
  `hex_color *= 2` with a per-character doubling, so `#F12` expands to `255,17,34`, and updates the test.
- `fixjury verify` on that run: `automated_pass=True` with `tests`, `lint`, `types`, `policy` and
  `heldout` all true, tool versions recorded (`python 3.12.16`, `pytest 8.4.1`, `mypy 1.18.2`). The
  platform's own `git diff` carried no platform files, so the workspace stash works for this harness too.

## Not done, and why

- **The 32 OpenHands pairs are not run yet.** This change makes the third executor work; the grid
  (`fixjury bench --agents baseline,mini,openhands`) is the next step, and until it lands the corpus has
  65 of 96 pairs.
- **No condenser.** The OpenHands CLI installs a summarizing condenser; the driver does not, to keep the
  agent's model calls comparable with mini's and to avoid a second usage bucket. If long tasks start
  hitting the context window, this is the knob — and the run above used 477k prompt tokens in 30 calls,
  so it is worth watching rather than assuming.
