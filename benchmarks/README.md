# The frozen corpus

Thirty real Python defects, each frozen at the commit that preceded its upstream fix. Every
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
