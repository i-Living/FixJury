import type { Metadata } from "next";
import type { ReactNode } from "react";

import { loadDashboard } from "@/lib/data";

import "./globals.css";

export function generateMetadata(): Metadata {
  const { title } = loadDashboard();
  return {
    title,
    description:
      "How autonomous coding agents repair frozen defects: acceptance, regressions, time and cost per accepted fix.",
  };
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
