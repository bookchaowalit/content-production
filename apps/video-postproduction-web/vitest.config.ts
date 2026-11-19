import {fileURLToPath} from "node:url";

import {defineConfig} from "vitest/config";

// SPEC-001 R-CI-4: the new server-side files are listed so a file no test loads still counts (as 0 %), and each one
// must reach 90 % of lines on its own.
const NEW_FILES = [
  "lib/server/proxy.ts",
  "lib/server/report-error.ts",
  "server/guard.mjs",
  "server/launch.mjs",
  "app/api/v1/[...path]/route.ts",
  "app/healthz/route.ts",
];

export default defineConfig({
  resolve: {alias: {"@": fileURLToPath(new URL("./", import.meta.url))}},
  test: {
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      // server-launch.mjs is a three-line wrapper around server/launch.mjs (tested in-process).
      include: ["lib/**/*.ts", "server/**/*.mjs", "app/api/**/route.ts", "app/healthz/route.ts"],
      exclude: ["**/*.test.*"],
      thresholds: Object.fromEntries(NEW_FILES.map((file) => [file, {lines: 90}])),
    },
  },
});
