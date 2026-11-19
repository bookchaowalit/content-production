import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // SPEC-001 R-NONROOT: the image runs server-launch.mjs around the standalone server.js.
  output: "standalone",
};

export default nextConfig;
