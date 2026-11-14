# Phase 5.3 — publication export and the mount guard

Plan for the two `docs/PLAN.md` items that were left open around the benchmark run: the raw-data
publication (`§5 Публикация`, `§11 5.3`) and the CI guard that proves an agent container never gets a
mount from `benchmarks/` (`§5`, `§11 1.5`). Both are code and neither needs a provider key, so they can
be proven on the data that already exists.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| 5.3 | `src/fixjury/export.py` — `runs`, `verdicts` and `rejections` as JSONL, deterministic order and encoding | unit tests on the line format and the digest; two exports of one database are byte-identical |
| 5.3 | `fixjury export --out <dir>` — writes the three files plus `manifest.json` with row counts and sha256 | integration test on live Postgres, work rolled back; a real run over the 64 stored pairs |
| 5.3 | the export verifies itself: row count against its table, digest re-read from disk | a mismatch raises `ExportError` instead of producing a manifest that says `verified` |
| 1.5 | `tests/unit/test_sandbox_mounts.py` — the agent container gets one bind, it is the workspace, and no source lies under `benchmarks/` | the guard runs in CI with the rest of the unit tests |

## Design

**The export is evidence, so it includes the diff.** `runs.jsonl` carries every column of `runs`,
`diff` and `unable_reason` included: the published numbers are only checkable if the patch they came
from is in the same file. `verdicts.jsonl` carries automation, reviewer and human fields; the human
columns are there even when null, because their absence is itself a fact about the pair.

**Determinism is the contract.** Rows are ordered by `(task_id, agent, attempt, id)`, keys are sorted,
separators are tight and the encoding is UTF-8 without ASCII escaping. Exporting one database twice
produces the same bytes — a publication that changes between two exports of the same data cannot be
used as evidence.

**`verified: true` is earned, not asserted.** After writing, the export counts the rows in the file,
re-hashes it from disk and compares both against what the database returned before the manifest is
written. A mismatch raises, so a manifest that exists means the check passed.

**File IO runs off the event loop.** The writing and verifying block is one `asyncio.to_thread` call;
the session stays in the loop where it belongs.

**The guard is a tripwire, not a second implementation.** `_create_container` builds the bind map from
`SandboxSpec`; the test drives that function with a fake docker client and fails if a second mount
appears or if any mount source resolves under `benchmarks/`. Construction already makes the property
true; the test is what keeps a later refactor from quietly making it false.

## Verification checkpoint

- `uv run ruff check . && uv run ruff format --check . && uv run mypy src` clean.
- `uv run pytest -q tests/unit/test_export.py tests/unit/test_sandbox_mounts.py` green.
- `uv run pytest -q tests/integration/test_export_publication.py` green on live Postgres, rows rolled
  back: the manifest's counts equal the table counts, every digest equals `sha256sum` of the file, a
  human rejection appears with its taxonomy code, and two exports are byte-identical.

Measured on the real database:

```
fixjury export --out <scratch>
wrote runs.jsonl 64 row(s), verdicts.jsonl 64 row(s), rejections.jsonl 76 row(s) and manifest.json
manifest verified: true
sha256sum agrees: runs a20dd228…, verdicts f5c71839…, rejections bc7947d4…
```

## Not done, and why

- **Intra-rater kappa (`§4`, `§11 6.4`) is blocked on the schema, not on effort.** `verdicts` is keyed
  by `run_id`, so a second blind pass over the same patch would overwrite the first verdict instead of
  recording a second one. The plan needs a `pass_no` in the verdict key (or a second table) before
  `human_resolution` can ever be written; that is a schema change and `AGENTS.md` asks the owner first.
- **The API (`§9`, `§10`) is still absent.** `src/fixjury/api/` does not exist; FastAPI is used only by
  the review page.
- **OpenHands (`§2`, `§11 4.2`) is still configuration only.** `openhands_image` is empty, so the third
  executor of the 96-pair grid has never run.
- **Deployment (`§11 7.3`) is not part of this change.** `fixjury dashboard` builds the page; serving it
  from nginx on the VPS is a separate, owner-approved step.
