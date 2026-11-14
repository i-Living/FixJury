import type { Interval } from "@/lib/data";

// Formatting only. A null renders as the word null, never as a zero: a zero
// denominator is not zero cost, and the page must not imply otherwise.

export const NULL_TEXT = "null";

export function percent(value: number | null | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : NULL_TEXT;
}

export function interval(value: Interval | null | undefined): string {
  return value
    ? `${(value.low * 100).toFixed(1)}–${(value.high * 100).toFixed(1)}%`
    : NULL_TEXT;
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

export function seconds(value: number | null | undefined): string {
  return typeof value === "number" ? `${value.toFixed(1)} s` : NULL_TEXT;
}

// A snapshot written before a field existed has no key for it, which arrives here as
// undefined. Unknown is rendered like null, never as a zero: the page cannot tell
// "nothing was reviewed" apart from "this build does not know".
export function count(value: number | null | undefined): string {
  return typeof value === "number" ? value.toLocaleString("en-US") : NULL_TEXT;
}

export function exclusions(value: Record<string, number>): string {
  const pairs = Object.entries(value);
  return pairs.length === 0 ? "none" : pairs.map(([key, runs]) => `${key}=${runs}`).join(", ");
}
