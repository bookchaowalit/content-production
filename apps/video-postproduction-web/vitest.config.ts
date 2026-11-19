import {fileURLToPath} from "node:url";

import {defineConfig} from "vitest/config";

// SPEC-001 R-CI-4: the new server-side files are listed so a file no test loads still counts (as 0 %), and each one
// must reach 90 % of lines on its own.
const NEW_FILES = ["lib/server/proxy.ts", "server/guard.mjs"];

export default defineConfig({
  resolve: {alias: {"@": fileURLToPath(new URL("./", import.meta.url))}},
  test: {
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      include: ["lib/**/*.ts", "server/**/*.mjs"],
      exclude: ["**/*.test.*"],
      thresholds: Object.fromEntries(NEW_FILES.map((file) => [file, {lines: 90}])),
    },
  },
});
