import { readFileSync } from "node:fs";
import path from "node:path";

// The payload of one metric_snapshots row, as fixjury.metrics.compute.as_payload
// writes it, plus the corpus split fixjury.report.charts.corpus_split derives from
// the same denominators. Nothing here is computed: the page only formats.

export type Interval = { low: number; high: number };

export type Metrics = {
  human_acceptance_rate: number | null;
  human_acceptance_ci: Interval | null;
  regression_free_acceptance_rate: number | null;
  regression_free_acceptance_ci: Interval | null;
  task_success_rate: number | null;
  tokens_per_accepted_fix: string | null;
  cost_per_accepted_fix: string | null;
  median_time_to_repair_s: number | null;
  false_completion_rate: number | null;
  tokens_total: number | null;
  cost_total: string | null;
};

export type Denominators = {
  pairs: number;
  reviewed_pairs: number;
  accepted: number;
  regression_free_accepted: number;
  automated_pass_pairs: number;
  no_patch_pairs: number;
  declared_success_runs: number;
  missing_heldout_pairs: number;
};

/** A share of reviewed pairs, or null when nothing was reviewed: null is not zero. */
export type Split = {
  regression_free: number | null;
  with_regression: number | null;
  not_accepted: number | null;
};

export type AgentSnapshot = {
  agent: string;
  cost_basis: string;
  pricing_version: string | null;
  denominators: Denominators;
  exclusions: Record<string, number>;
  metrics: Metrics;
  split: Split;
};

export type DashboardData = {
  title: string;
  basis: string;
  generated_at: string;
  agents: AgentSnapshot[];
};

/**
 * The export `fixjury dashboard` writes for this build. DASHBOARD_DATA is set by the
 * command; the fallback lets `npm run dev` read a checked-out export from web/data/.
 */
export function loadDashboard(): DashboardData {
  const file = process.env.DASHBOARD_DATA ?? path.join(process.cwd(), "data", "snapshots.json");

  let raw: string;
  try {
    raw = readFileSync(file, "utf8");
  } catch {
    throw new Error(
      `no snapshot data at ${file}: run \`uv run fixjury dashboard\`, or set DASHBOARD_DATA`,
    );
  }

  const data = JSON.parse(raw) as DashboardData;
  if (!Array.isArray(data.agents) || data.agents.length === 0) {
    throw new Error(`the export at ${file} carries no agents: compute metrics first`);
  }
  return data;
}
