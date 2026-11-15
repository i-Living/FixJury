import { readFileSync } from "node:fs";
import path from "node:path";

// The payload of one metric_snapshots row, as fixjury.metrics.compute.as_payload
// writes it. Nothing here is computed: the page only formats. A payload written
// before V1.1 carries the reviewer's numbers under their old names and no
// verdict_source; normalize() maps it to the current shape so the page has one
// type to read and never guesses which series it is looking at.

export type Interval = { low: number; high: number };

export type Metrics = {
  acceptance_rate: number | null;
  acceptance_ci: Interval | null;
  regression_free_acceptance_rate: number | null;
  regression_free_acceptance_ci: Interval | null;
  task_success_rate: number | null;
  tokens_per_accepted_fix: string | null;
  cost_per_accepted_fix: string | null;
  median_time_to_repair_s: number | null;
  false_completion_rate: number | null;
  tokens_total: number | null;
  cost_total: string | null;
  judge_tokens_total: number | null;
  judge_cost_total: string | null;
};

export type Denominators = {
  pairs: number;
  decided_pairs: number;
  human_labeled_pairs: number | null;
  evaluated_pairs: number | null;
  accepted: number;
  regression_free_accepted: number;
  automated_pass_pairs: number;
  no_patch_pairs: number;
  declared_success_runs: number;
  missing_heldout_pairs: number;
};

/** A share of decided pairs, or null when nothing was decided: null is not zero. */
export type Split = {
  regression_free: number | null;
  with_regression: number | null;
  not_accepted: number | null;
};

/** One calibration rate: the count, its denominator and the share. */
export type Rate = { count: number; of: number; rate: number | null };

/** What the judge did, measured against the held-out labels it did not produce. */
export type Calibration = {
  reference: string;
  labeled_pairs: number;
  unlabeled_pairs: number;
  false_acceptance: Rate;
  false_rejection: Rate;
  agreement: Rate;
  escalation: Rate;
  brier: number | null;
  meets_thresholds: boolean | null;
};

export type AgentSnapshot = {
  agent: string;
  cost_basis: string;
  pricing_version: string | null;
  verdict_source: string;
  denominators: Denominators;
  exclusions: Record<string, number>;
  metrics: Metrics;
  split: Split;
  calibration?: Calibration;
};

export type DashboardData = {
  title: string;
  basis: string;
  generated_at: string;
  agents: AgentSnapshot[];
};

/** V1 snapshots measured the reviewer, and called the number human acceptance. */
const LEGACY_SOURCE = "human (V1 snapshot)";

type Raw = Record<string, unknown>;

function record(value: unknown): Raw {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Raw)
    : {};
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function intervalOrNull(value: unknown): Interval | null {
  const block = record(value);
  const low = numberOrNull(block.low);
  const high = numberOrNull(block.high);
  return low === null || high === null ? null : { low, high };
}

function rateOf(value: unknown): Rate {
  const block = record(value);
  return {
    count: numberOrNull(block.count) ?? 0,
    of: numberOrNull(block.of) ?? 0,
    rate: numberOrNull(block.rate),
  };
}

function calibrationOf(value: unknown): Calibration | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const block = record(value);
  // A payload with no labelled pair has numbers the page cannot compare; keep the
  // block anyway so the table can say "not measured" instead of showing a gap.
  return {
    reference: stringOrNull(block.reference) ?? "held-out suite pass/fail",
    labeled_pairs: numberOrNull(block.labeled_pairs) ?? 0,
    unlabeled_pairs: numberOrNull(block.unlabeled_pairs) ?? 0,
    false_acceptance: rateOf(block.false_acceptance),
    false_rejection: rateOf(block.false_rejection),
    agreement: rateOf(block.agreement),
    escalation: rateOf(block.escalation),
    brier: numberOrNull(block.brier),
    meets_thresholds:
      typeof block.meets_thresholds === "boolean" ? block.meets_thresholds : null,
  };
}

function normalize(raw: unknown): AgentSnapshot {
  const payload = record(raw);
  const metrics = record(payload.metrics);
  const denominators = record(payload.denominators);
  const split = record(payload.split);
  const exclusions = record(payload.exclusions);

  return {
    agent: stringOrNull(payload.agent) ?? "?",
    cost_basis: stringOrNull(payload.cost_basis) ?? "list_price",
    pricing_version: stringOrNull(payload.pricing_version),
    verdict_source: stringOrNull(payload.verdict_source) ?? LEGACY_SOURCE,
    denominators: {
      pairs: numberOrNull(denominators.pairs) ?? 0,
      decided_pairs: numberOrNull(denominators.decided_pairs ?? denominators.reviewed_pairs) ?? 0,
      human_labeled_pairs: numberOrNull(denominators.human_labeled_pairs),
      evaluated_pairs: numberOrNull(denominators.evaluated_pairs),
      accepted: numberOrNull(denominators.accepted) ?? 0,
      regression_free_accepted: numberOrNull(denominators.regression_free_accepted) ?? 0,
      automated_pass_pairs: numberOrNull(denominators.automated_pass_pairs) ?? 0,
      no_patch_pairs: numberOrNull(denominators.no_patch_pairs) ?? 0,
      declared_success_runs: numberOrNull(denominators.declared_success_runs) ?? 0,
      missing_heldout_pairs: numberOrNull(denominators.missing_heldout_pairs) ?? 0,
    },
    exclusions: Object.fromEntries(
      Object.entries(exclusions).map(([key, value]) => [key, numberOrNull(value) ?? 0]),
    ),
    metrics: {
      acceptance_rate: numberOrNull(metrics.acceptance_rate ?? metrics.human_acceptance_rate),
      acceptance_ci: intervalOrNull(metrics.acceptance_ci ?? metrics.human_acceptance_ci),
      regression_free_acceptance_rate: numberOrNull(metrics.regression_free_acceptance_rate),
      regression_free_acceptance_ci: intervalOrNull(metrics.regression_free_acceptance_ci),
      task_success_rate: numberOrNull(metrics.task_success_rate),
      tokens_per_accepted_fix: stringOrNull(metrics.tokens_per_accepted_fix),
      cost_per_accepted_fix: stringOrNull(metrics.cost_per_accepted_fix),
      median_time_to_repair_s: numberOrNull(metrics.median_time_to_repair_s),
      false_completion_rate: numberOrNull(metrics.false_completion_rate),
      tokens_total: numberOrNull(metrics.tokens_total),
      cost_total: stringOrNull(metrics.cost_total),
      judge_tokens_total: numberOrNull(metrics.judge_tokens_total),
      judge_cost_total: stringOrNull(metrics.judge_cost_total),
    },
    split: {
      regression_free: numberOrNull(split.regression_free),
      with_regression: numberOrNull(split.with_regression),
      not_accepted: numberOrNull(split.not_accepted),
    },
    calibration: calibrationOf(payload.calibration),
  };
}

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

  const data = JSON.parse(raw) as Raw;
  const agents = Array.isArray(data.agents) ? data.agents.map(normalize) : [];
  if (agents.length === 0) {
    throw new Error(`the export at ${file} carries no agents: compute metrics first`);
  }
  return {
    title: stringOrNull(data.title) ?? "FixJury — agent repair benchmark",
    basis: stringOrNull(data.basis) ?? "list_price",
    generated_at: stringOrNull(data.generated_at) ?? "",
    agents,
  };
}
