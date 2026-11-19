// Refuse to start with missing or unknown access settings (SPEC-001 R-TS-4); the launcher checks too.
// Report server errors to Sentry, errors only (R-OBS-3).
import {loadConfig} from "@/server/guard.mjs";
import {reportServerError} from "@/lib/server/report-error";

export function register(): void {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    loadConfig(process.env);
  }
}

export async function onRequestError(
  error: unknown,
  request: {path: string; method: string},
): Promise<void> {
  await reportServerError(
    error,
    {path: request.path, method: request.method},
    {SENTRY_DSN: process.env.SENTRY_DSN, SENTRY_ENVIRONMENT: process.env.SENTRY_ENVIRONMENT},
  );
}
