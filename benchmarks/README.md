# The frozen corpus

Thirty-two real Python defects, each frozen at the commit that preceded its upstream fix. Every
task in this directory is data, not code: the loader, validator and runner live in
`src/fixjury/tasks/`.

## Layout

```
benchmarks/
├── index.json                  corpus manifest: version, freeze date, task ids
├── tasks/T-001/task.yaml       the frozen RepairSpec (PLAN §8)
├── drafts/T-001.yaml           the draft a human approved before freezing
└── heldout/T-001/
    ├── heldout.yaml            pytest command plus the files the bundle provides
    ├── files/<repo path>       post-fix copies of the test files the upstream fix touched
    └── sanity.json             evidence that the task behaves as PLAN §5 requires
```

`benchmarks/heldout/` is **public by design**. Isolation comes from the setup, not from
secrecy: an agent receives a checkout of the *target* repository at `base_commit` with no
network, so this directory is never inside its container. Verification copies
`heldout/<id>/files/` over its own checkout, which is why an agent editing the same test file
cannot change what the held-out suite does.

## What makes a task valid

`uv run fixjury tasks validate benchmarks` enforces all of it:

- required keys per PLAN §8, a license from `MIT` / `Apache-2.0` / `BSD-3-Clause`, a full
  40-character `base_commit`, an issue snapshot with both dates;
- issue created and fix merged no earlier than **2025-06-01** (contamination cut-off);
- `checks` limited to `pytest`, `ruff check` and `mypy` with relative paths and no flags;
- `allowed_paths` free of `benchmarks/`, and the held-out bundle consistent with
  `heldout.yaml`;
- `sanity.json` recorded for the same `base_commit` and `fix_commit`, with all four stages as
  expected.

The four stages in `sanity.json` come from `scripts/corpus_sanity.py`, which builds a throwaway
environment per task and requires:

| stage | expectation | why |
| --- | --- | --- |
| `base_checks` | exit 0 | a no-op baseline must not flake |
| `base_heldout` | non-zero | the held-out suite really covers the defect |
| `fix_checks` | exit 0 | the reference fix does not break the repository |
| `fix_heldout` | exit 0 | the reference fix makes the held-out suite pass |

This is corpus QA on the host, not verification: verdicts are produced later, in a fresh
container, by the phase 3 runner.

## Recorded metadata

V1.1 decided not to grow the corpus (PLAN_V1.1 decision 2), so a leaderboard can only group by
what the frozen files record today. This is that inventory, audited at `corpus-1`:

| Field | Where it lives | Values in `corpus-1` |
| --- | --- | --- |
| `corpus_version` | `index.json` | `corpus-1`, frozen `2026-11-12` |
| task count | `index.json` | 32, across 8 repositories |
| `difficulty` | `tasks/<id>/task.yaml` | 18 `medium`, 8 `easy`, 6 `hard` |
| `license` | `tasks/<id>/task.yaml` | 28 MIT, 3 BSD-3-Clause, 1 Apache-2.0 |
| `base_commit`, `fix_commit` | `tasks/<id>/task.yaml` | one full sha each, both non-empty |
| `checks`, `allowed_paths` | `tasks/<id>/task.yaml` | pytest / ruff / mypy, relative paths only |
| held-out bundle | `heldout/<id>/` | 32 of 32 with `sanity.json` |
| language | — | **not recorded per task**; every task is a Python repository |
| task category | — | **not recorded per task** |

Two consequences, both visible in the artefacts rather than papered over:

- `fixjury leaderboard` ranks `overall` and `difficulty`, and its output states that language
  and category have no ranking because the corpus does not record them. Adding the field would
  mean editing a frozen `task.yaml`, which AGENTS.md invariant 9 forbids once a run exists: it
  would be a new task version, not a metadata patch.
- every leaderboard entry carries the `corpus_version`, the run `config_hash`, the verdict
  source and the judge configuration hash, so two rankings are only comparable when they agree
  on all four.

Reproducibility is the `sanity.json` record: `base_checks` must exit 0 on a pristine checkout,
which is the no-op baseline that proves a task does not flake on its own tests before any agent
touches it. `uv run fixjury tasks validate benchmarks` refuses a task whose `sanity.json` is
missing, stale for the recorded commits, or says a stage behaved otherwise.

## Adding a task

1. `uv run fixjury tasks draft --repo <url> --issue <n> --id T-031 --fix-pr <pr>` and edit the
   draft; `allowed_paths` may only cover files the upstream fix touched.
2. Write `heldout/T-031/` by hand or from the fix commit: `heldout.yaml` plus
   `files/<repo path>` with the post-fix test files.
3. `uv run python scripts/corpus_sanity.py --corpus benchmarks --task T-031` — it writes
   `sanity.json`.
4. `uv run fixjury tasks freeze --draft drafts/T-031.yaml` — copies license, commits and dates
   from GitHub and writes `tasks/T-031/task.yaml`.
5. Add the id to `index.json` and run `uv run fixjury tasks validate benchmarks`.

## Frozen means frozen

Once any run exists for a task, `task.yaml` is immutable: a change needs a new version
(`T-014-v2`) and a fresh run. `fixjury tasks freeze` refuses to overwrite a frozen file for the
same reason.
