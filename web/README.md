# web/ — the read-only metrics page

The public page from `docs/PLAN.md` §7.3: a Next.js app exported as static files and served by nginx.
It shows one row per agent — acceptance with its Wilson interval, regression-free acceptance, cost per
accepted fix, and where the corpus ends up — from the rows in `metric_snapshots`.

## The one rule

**No metric is computed here.** The page formats and draws numbers that Python already computed once
(`src/fixjury/metrics/`) and stored. The build reads a single JSON export written by
`src/fixjury/report/dashboard.py`:

```json
{
  "title": "…",
  "basis": "list_price",
  "generated_at": "2026-11-13 09:00 UTC",
  "agents": [
    { "agent": "baseline", "cost_basis": "…", "pricing_version": "…",
      "verdict_source": "judge",
      "denominators": { "pairs": 6, "decided_pairs": 6, "accepted": 4, "…": 0 },
      "exclusions": { "infra_failed": 2 },
      "metrics": { "acceptance_rate": 0.667, "acceptance_ci": { "low": 0.268, "high": 0.925 }, "…": null },
      "split": { "regression_free": 0.5, "with_regression": 0.167, "not_accepted": 0.333 },
      "calibration": { "labeled_pairs": 6, "false_acceptance": { "count": 0, "of": 2, "rate": 0.0 }, "…": null } }
  ]
}
```

Every block above is computed once, in `src/fixjury/metrics/`, and stored with the snapshot; the page
only formats. A snapshot written before V1.1 has no `verdict_source` and no `calibration`, and
`web/lib/data.ts` maps its `human_acceptance_rate` / `reviewed_pairs` onto the current names — a
payload without the field is, by definition, the human series. A zero denominator arrives as `null`
and renders as the word `null`, never as `0`.

## Commands

Everything you normally need goes through the CLI, from the repository root:

```bash
(cd web && npm ci)                # once, Node 20.9 or newer; the subshell keeps the repo root
uv run fixjury dashboard --out docs/dashboard
```

That command writes the export, runs `npm run build` here with `DASHBOARD_DATA` pointing at it, and
copies the static export (`web/out/`) to `--out`. Serving `--out` with nginx is the whole deployment.

Working on the page itself:

```bash
npm run dev        # http://localhost:3000
npm run typecheck  # tsc --noEmit
npm run build      # full static export into web/out, needs DASHBOARD_DATA
```

`npm run dev` without `DASHBOARD_DATA` reads `web/data/snapshots.json`, which you can write with
`uv run python -c "…"` or copy from an export; it is gitignored.

## Layout

```
app/page.tsx                 the page: tables, chart cards, the notes; prerendered
app/layout.tsx               metadata and the stylesheet
components/metric-charts.tsx the only client component: four Chart.js plots
lib/data.ts                  the export's types and where it is read from
lib/format.ts                percent, interval, amount, seconds — null stays null
```

## Deployment

Static files, no server, no runtime data source: `docs/dashboard/` goes behind nginx exactly as
`docs/report/` does. Deploying it is an owner decision (`AGENTS.md`, "Ask first").
