// Launcher logic (SPEC-001 R-PROXY-2, R-TS-5), kept importable so it is tested in-process.
import {loadConfig, patchCreateServer} from "./guard.mjs";

export async function launch({env, http, importServer, log = console}) {
  let config;
  try {
    config = loadConfig(env);
  } catch (error) {
    log.error(`video studio web refuses to start: ${error.message}`);
    return 1;
  }
  if (config.mode === "off") log.warn("STUDIO_ACCESS_MODE=off: no identity check (local use only)");
  patchCreateServer(http, config);
  await importServer();
  return 0;
}
