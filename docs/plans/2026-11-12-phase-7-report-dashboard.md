# Phase 7.2 (charts, reproducible report) and 7.3 (static dashboard)

Closes the two report items that PLAN §11 leaves open after the metrics work, plus the `--snapshot`
reproduction the plan's own check asks for. Deployment is not part of this change.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| 7.2 | `src/fixjury/report/charts.py` — acceptance with its Wilson interval, regression-free acceptance, cost per accepted fix, and where the corpus ends up | charts render to real PNGs; a `null` cost draws a gap labelled `n/a`, never a zero bar |
| 7.2 | `fixjury report --snapshot <id> --charts` — reproduce a report from one stored snapshot, charts included | the command writes the four PNGs beside `README.md` |
| 7.3 | `web/` (Next.js, static export) + `src/fixjury/report/dashboard.py` + `fixjury dashboard` — a build-time page from the snapshots: headline table, denominators, four Chart.js charts | the page has no external requests, escapes agent names, and shows `null` where a denominator was zero |

## Design

**Charts answer one question each.** Acceptance carries its interval, because a rate on ten pairs
without its interval invites a comparison the data cannot support. The stacked chart shows the whole
corpus — regression-free, accepted with a regression, not accepted — so the acceptance bar cannot be
read as the whole story. Segment labels sit inside the bars, so colour is not the only encoding.

**A missing value is a gap, not a zero.** `cost_per_accepted_fix` with nothing accepted is `null`; the
bar is drawn empty with an `n/a` label, because a bar at zero claims the fix was free.

**The page is a Next.js static export.** `web/` holds the app; `next build` prerenders it to plain
files, so serving it is still nginx pointing at a directory. The numbers reach the build through one
JSON export written by `fixjury.report.dashboard`, which carries the stored snapshot payload plus the
corpus split from `corpus_split()` — the same function the PNG charts stack their bars with. No metric
is computed in TypeScript: the page formats and draws, so invariant 5 holds for the page exactly as it
holds for the report.

**Charts are client-side and the numbers are not.** Chart.js draws the four plots from the same export.
Everything behind them — every rate, interval, count and the corpus split — is in the prerendered HTML,
so the page reads correctly with JavaScript off; only the plots themselves need it. The chart key is
HTML, not canvas legend, so colour is never the only encoding.

## Decision taken

This started as a Python HTML string with base64 PNGs, because that kept the build chain inside `uv`.
The owner chose the stack `AGENTS.md` and `docs/PLAN.md` §7.3 already name, so the page moved to
Next.js + Chart.js. The Python side kept only what it must own: the export and the corpus arithmetic.

## Verification checkpoint

- `uv run pytest -q` green; `uv run mypy src tests` clean.
- Two snapshots in the database → `uv run fixjury dashboard --out <dir>` builds the Next.js export and
  writes `index.html` there with both rows, four charts, the pricing version and the exclusions. Verified
  on real snapshots, then the demo rows were deleted.
- `fixjury report --snapshot <id>` renders that snapshot alone, so an old report can be reproduced
  without depending on what ran afterwards.
- A hostile agent name is escaped (`<script>` arrives as `&lt;script&gt;`), the page makes no external
  request, and `null` renders as the word `null` rather than a zero.
- The four charts were opened in a browser at 390 px and 1360 px: bars match the numbers, axis labels
  are agent names, the `null` cost is a gap and not a zero bar.
