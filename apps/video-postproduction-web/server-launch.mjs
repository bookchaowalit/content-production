// Production start command for the web image (SPEC-001 R-PROXY-2, R-TS-5): validate access settings, guard every
// request at the HTTP entry, set long request / short idle timeouts, then start Next's standalone server.
import http from "node:http";

import {loadConfig, patchCreateServer} from "./server/guard.mjs";

let config;
try {
  config = loadConfig(process.env);
} catch (error) {
  console.error(`video studio web refuses to start: ${error.message}`);
  process.exit(1);
}
if (config.mode === "off") console.warn("STUDIO_ACCESS_MODE=off: no identity check (local use only)");
patchCreateServer(http, config);
await import("./server.js");
