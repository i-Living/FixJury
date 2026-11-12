# Phase 7.2 (charts, reproducible report) and 7.3 (static dashboard)

Closes the two report items that PLAN §11 leaves open after the metrics work, plus the `--snapshot`
reproduction the plan's own check asks for. Deployment is not part of this change.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| 7.2 | `src/fixjury/report/charts.py` — acceptance with its Wilson interval, regression-free acceptance, cost per accepted fix, and where the corpus ends up | charts render to real PNGs; a `null` cost draws a gap labelled `n/a`, never a zero bar |
| 7.2 | `fixjury report --snapshot <id> --charts` — reproduce a report from one stored snapshot, charts included | the command writes the four PNGs beside `README.md` |
| 7.3 | `src/fixjury/report/dashboard.py` + `fixjury dashboard` — a self-contained page from the snapshots: headline table, denominators, charts embedded as base64 | the page has no external requests, escapes agent names, and shows `null` where a denominator was zero |

## Design

**Charts answer one question each.** Acceptance carries its interval, because a rate on ten pairs
without its interval invites a comparison the data cannot support. The stacked chart shows the whole
corpus — regression-free, accepted with a regression, not accepted — so the acceptance bar cannot be
read as the whole story. Segment labels sit inside the bars, so colour is not the only encoding.

**A missing value is a gap, not a zero.** `cost_per_accepted_fix` with nothing accepted is `null`; the
bar is drawn empty with an `n/a` label, because a bar at zero claims the fix was free.

**The page is one file.** Charts are base64-embedded PNGs and there is no script, so serving it is
nginx pointing at a directory and refreshing it is re-running the command. Nothing on the page
recomputes a metric: it renders the payload the snapshot already carries, which is invariant 5.

## Deviation to settle

`AGENTS.md` names Next.js for this page. This change builds the static export in Python instead: the
artefact is the same (static files behind nginx), the dependency and build chain are not. That is a
stack decision, so it stays an explicit question rather than a silent change — `AGENTS.md` is
untouched.

## Verification checkpoint

- `uv run pytest -q` green; `uv run mypy src tests` clean.
- Two synthetic agents → two snapshots → `fixjury dashboard` writes `index.html` with both rows, four
  embedded charts, the pricing version and the exclusions.
- `fixjury report --snapshot <id>` renders that snapshot alone, so an old report can be reproduced
  without depending on what ran afterwards.
- A hostile agent name is escaped, and the page contains no `<script>` and no external URL.
