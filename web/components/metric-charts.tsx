"use client";

import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  LinearScale,
  Tooltip,
  type ChartConfiguration,
  type TooltipItem,
} from "chart.js";
import { useEffect, useMemo, useRef } from "react";

import type { AgentSnapshot } from "@/lib/data";

Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip);

// Same palette as the matplotlib charts in src/fixjury/report/charts.py, so the
// page and the PNG report cannot look like two different measurements.
const INK = "#1d1d1b";
const MUTED = "#8a8a83";
const RULE = "#ecece6";
const GOOD = "#3f7d52";
const CAUTION = "#b7791f";
const BAD = "#a4493c";

const FONT = {
  family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  size: 10,
};

const categories = {
  grid: { display: false },
  border: { display: false },
  ticks: { color: INK, font: FONT },
};

const percentAxis = {
  min: 0,
  max: 1,
  border: { display: false },
  grid: { color: RULE, drawTicks: false },
  ticks: {
    color: MUTED,
    font: FONT,
    stepSize: 0.25,
    callback: (value: string | number) => `${Math.round(Number(value) * 100)}%`,
  },
};

// null is a gap with the word null in the tooltip, never a bar at zero: a bar at
// zero says "this fix cost nothing", which is a different claim.
function percentTooltip(item: TooltipItem<"bar">): string {
  const raw = item.raw as number[] | number | null;
  const label = item.dataset.label ?? "";
  if (raw === null || raw === undefined) return `${label}: null`;
  if (Array.isArray(raw)) {
    return `${label}: ${((raw[0] ?? 0) * 100).toFixed(1)}–${((raw[1] ?? 0) * 100).toFixed(1)}%`;
  }
  return `${label}: ${(raw * 100).toFixed(1)}%`;
}

export type ChartKind = "acceptance" | "regression_free" | "cost" | "corpus";

function acceptanceChart(agents: AgentSnapshot[]): ChartConfiguration<"bar"> {
  return {
    type: "bar",
    data: {
      labels: agents.map((agent) => agent.agent),
      datasets: [
        {
          // A floating bar from the lower to the upper bound: the interval is the
          // shape, the estimate is the marker drawn over it.
          label: "95% interval",
          data: agents.map((agent) => {
            const ci = agent.metrics.human_acceptance_ci;
            return ci === null ? null : [ci.low, ci.high];
          }),
          backgroundColor: "rgba(63, 125, 82, 0.16)",
          borderColor: "rgba(63, 125, 82, 0.45)",
          borderWidth: 1,
          barThickness: 26,
          grouped: false,
        },
        {
          label: "Acceptance",
          data: agents.map((agent) => {
            const rate = agent.metrics.human_acceptance_rate;
            return rate === null ? null : [rate - 0.004, rate + 0.004];
          }),
          backgroundColor: GOOD,
          barThickness: 26,
          grouped: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: { x: categories, y: percentAxis },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: percentTooltip } } },
    },
  };
}

function regressionFreeChart(agents: AgentSnapshot[]): ChartConfiguration<"bar"> {
  return {
    type: "bar",
    data: {
      labels: agents.map((agent) => agent.agent),
      datasets: [
        {
          label: "Regression-free acceptance",
          data: agents.map((agent) => agent.metrics.regression_free_acceptance_rate),
          backgroundColor: GOOD,
          barThickness: 34,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: { x: categories, y: percentAxis },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: percentTooltip } } },
    },
  };
}

function costChart(agents: AgentSnapshot[]): ChartConfiguration<"bar"> {
  return {
    type: "bar",
    data: {
      labels: agents.map((agent) => agent.agent),
      datasets: [
        {
          label: "Cost per accepted fix, USD",
          data: agents.map((agent) => {
            const value = agent.metrics.cost_per_accepted_fix;
            return value === null ? null : Number(value);
          }),
          backgroundColor: CAUTION,
          barThickness: 34,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: categories,
        y: {
          beginAtZero: true,
          border: { display: false },
          grid: { color: RULE, drawTicks: false },
          ticks: { color: MUTED, font: FONT },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (item) => {
              const raw = item.raw as number | null;
              const label = item.dataset.label ?? "";
              return raw === null ? `${label}: null` : `${label}: ${raw.toFixed(2)}`;
            },
          },
        },
      },
    },
  };
}

function corpusChart(agents: AgentSnapshot[]): ChartConfiguration<"bar"> {
  return {
    type: "bar",
    data: {
      labels: agents.map((agent) => agent.agent),
      datasets: [
        {
          label: "accepted, regression-free",
          data: agents.map((agent) => agent.split.regression_free),
          backgroundColor: GOOD,
          barThickness: 34,
        },
        {
          label: "accepted, regression",
          data: agents.map((agent) => agent.split.with_regression),
          backgroundColor: CAUTION,
          barThickness: 34,
        },
        {
          label: "not accepted",
          data: agents.map((agent) => agent.split.not_accepted),
          backgroundColor: BAD,
          barThickness: 34,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: { x: { stacked: true, ...categories }, y: { stacked: true, ...percentAxis } },
      plugins: {
        // The key is HTML, under the plot: readable without JavaScript and it does
        // not spend plot width on three long labels.
        legend: { display: false },
        tooltip: { callbacks: { label: percentTooltip } },
      },
    },
  };
}

function build(kind: ChartKind, agents: AgentSnapshot[]): ChartConfiguration<"bar"> {
  switch (kind) {
    case "acceptance":
      return acceptanceChart(agents);
    case "regression_free":
      return regressionFreeChart(agents);
    case "cost":
      return costChart(agents);
    case "corpus":
      return corpusChart(agents);
  }
}

export function MetricChart({
  kind,
  agents,
  label,
}: {
  kind: ChartKind;
  agents: AgentSnapshot[];
  label: string;
}) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const config = useMemo(() => build(kind, agents), [kind, agents]);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const chart = new Chart(canvas, config);
    return () => chart.destroy();
  }, [config]);

  return <canvas ref={ref} role="img" aria-label={label} />;
}
