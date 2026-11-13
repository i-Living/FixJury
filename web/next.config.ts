import type { NextConfig } from "next";

// The page is a static export: `output: "export"` makes `next build` write plain
// files to web/out, and `fixjury dashboard` copies them to the target directory.
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default config;
