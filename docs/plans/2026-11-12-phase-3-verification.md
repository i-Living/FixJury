# Phase 3 — verification

Plan for `docs/PLAN.md` §11 phase 3. Order follows the numbering here: policy and verdict
assembly are pure and testable without docker, so they land first and the container runner is
built against them.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| 3.3 | `src/fixjury/verify/policy.py` — `allowed_paths`, file-count limit, protected CI/config/lock files, no test deletion, no moving a test out of the tree | one negative fixture per rule, plus a clean patch that passes |
| 3.4 | `src/fixjury/verify/verdict.py` — `automated_pass = tests ∧ lint ∧ type ∧ policy ∧ (heldout ≠ false)`, `tool_versions`, rejection codes | truth table over every boolean, including `heldout_pass = null` |
| 3.1 | `src/fixjury/verify/runner.py` — fresh checkout, apply the diff, install, run the task's checks, pinned tool versions | a patch that does not apply is a `BUILD_BREAK`; a failing check blocks the pass |
| 3.2 | held-out overlay, copied **after** the task's checks run | a cosmetic patch passes `checks` and fails `heldout`; no bundle gives `null`, not `false` |
| — | `src/fixjury/verify/service.py` + `fixjury verify --run <id>` | verifying twice keeps one verdict and one rejection per violation |

## Non-negotiables carried in from `AGENTS.md`

- Verification runs in its own container, never inside the agent's container.
- The held-out bundle is copied into the verification checkout only; the agent never sees it.
- The diff is applied to a pristine checkout of `base_commit`; nothing is verified in place.
- `tool_versions` is recorded on every verdict, because an unpinned upgrade silently rewrites history.
- A zero denominator is not zero: `heldout_pass = null` is not `false`.

## Design

### Two containers, and why

Installing a project's dependencies needs the network. Executing an untrusted patch does not.
Verification therefore uses:

1. a **prepare** container with network, which creates `.venv` inside the workspace and installs
   the project (`pip install -e .[dev]`, falling back to `-e .` when there is no dev extra);
2. a **verify** container without network, which runs the task's checks, then the held-out suite,
   and reads tool versions.

The agent's container stays `network_mode="none"` — `SandboxSpec.network` exists for the prepare
container only, and defaults to `False`.

### Order inside the verify container

1. run every command from `task.yaml: checks` against the agent's tree → `tests_pass`, `lint_pass`,
   `type_pass`;
2. copy `benchmarks/heldout/<id>/files/**` over the checkout;
3. run `pytest <heldout files>` → `heldout_pass`.

Copying first would make `tests_pass` and `heldout_pass` the same measurement for tasks whose
`checks` point at the file the bundle provides, and would hide a patch that broke the repository's
own tests while satisfying the held-out ones.

### Rejection codes

| Rule | Code |
| --- | --- |
| empty diff | `INCOMPLETE_FIX` |
| path outside `allowed_paths`, or too many files | `EXTRA_FILES` |
| CI, configuration or lock file touched; forbidden path; test deleted or moved out of the tree | `SPEC_VIOLATION` |
| a `checks` command failed, or the diff does not apply | `BUILD_BREAK` |
| held-out suite still fails | `INCOMPLETE_FIX` |

## Verification checkpoint

- `uv run pytest -q` green; `uv run mypy src tests` clean.
- Reference fix for T-001 → `automated_pass = true` with `heldout_pass = true`.
- Cosmetic patch for T-001 → `checks` pass, `heldout_pass = false`, `automated_pass = false`.
- Running `fixjury verify --run <id>` twice leaves one `verdicts` row and one rejection per
  violation.
- No module under `src/fixjury/verify/` writes to the database except `service.py`.

## Checkpoint — measured

Run against task `T-001` (loguru, `base_commit 36da8cc`) with the real corpus bundle, in the real
container, through the real CLI:

| Run | Diff | Verdict |
| --- | --- | --- |
| reference fix (`2a2c59e`, code + test only) | 79 lines, 2 files | `automated=true tests=true lint=true types=true policy=true heldout=true`, no rejections |
| cosmetic patch (a comment in `_colorizer.py`) | 10 lines, 1 file | `automated=false tests=true lint=true types=true policy=true heldout=false`, rejection `INCOMPLETE_FIX: the held-out suite still fails` |

The second row is the point of the phase: the repository's own checks pass, policy passes, and the
held-out suite is what catches the patch. `tool_versions` from the reference run:
`python 3.12.16`, `pytest 8.4.1`, `mypy 1.18.2`.

Verifying the same run twice left **one** `verdicts` row with identical values, which is the
idempotency check from task 3.1.

Two findings from the live run, both fixed here:

- the reference fix also edits `CHANGELOG.rst`, which is outside `allowed_paths`; policy would reject
  the full upstream commit with `EXTRA_FILES`. That is the intended rule — an agent editing a
  changelog is not fixing the defect — and the reference diff used for the check is the code and
  test part only.
- the held-out pytest run was initially counted as one of the task's checks, so a cosmetic patch
  showed `tests=false`. It now carries `heldout=True` and feeds only `heldout_pass`.

Not done, deliberately: no reviewer pass, no rubric, no blind packaging (phase 6); no metrics
(phase 7). Verification currently re-installs dependencies on every call — correct, and slow enough
that a cached image is the obvious optimisation for the 90-run benchmark.
