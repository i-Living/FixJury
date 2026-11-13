import type { Interval } from "@/lib/data";

// Formatting only. A null renders as the word null, never as a zero: a zero
// denominator is not zero cost, and the page must not imply otherwise.

export const NULL_TEXT = "null";

export function percent(value: number | null): string {
  return value === null ? NULL_TEXT : `${(value * 100).toFixed(1)}%`;
}

export function interval(value: Interval | null): string {
  return value === null ? NULL_TEXT : `${(value.low * 100).toFixed(1)}–${(value.high * 100).toFixed(1)}%`;
}

/** Numeric strings (tokens, cost) arrive as "1250.00"; show them with grouping. */
export function amount(value: string | number | null, decimals = 2): string {
  if (value === null) return NULL_TEXT;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    : String(value);
}

export function seconds(value: number | null): string {
  return value === null ? NULL_TEXT : `${value.toFixed(1)} s`;
}

export function count(value: number | null): string {
  return value === null ? NULL_TEXT : value.toLocaleString("en-US");
}

export function exclusions(value: Record<string, number>): string {
  const pairs = Object.entries(value);
  return pairs.length === 0 ? "none" : pairs.map(([key, runs]) => `${key}=${runs}`).join(", ");
}
