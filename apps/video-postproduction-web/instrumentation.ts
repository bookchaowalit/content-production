// Refuse to start with missing or unknown access settings (SPEC-001 R-TS-4); the launcher checks too.
import {loadConfig} from "@/server/guard.mjs";

export function register(): void {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    loadConfig(process.env);
  }
}
