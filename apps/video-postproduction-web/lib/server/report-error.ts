// Errors-only Sentry reporting for the web server (SPEC-001 R-OBS-3), without the SDK: one envelope per error with
// the exception type, the route path (no query string) and nothing from the request. Never throws.

export type ErrorContext = {path?: string; method?: string};

function parseDsn(dsn: string): {endpoint: string; key: string} | null {
  try {
    const url = new URL(dsn);
    const project = url.pathname.replace(/^\/+/, "");
    if (!url.username || !project) return null;
    return {endpoint: `${url.protocol}//${url.host}/api/${project}/envelope/`, key: url.username};
  } catch {
    return null;
  }
}

export async function reportServerError(
  error: unknown,
  context: ErrorContext,
  env: {SENTRY_DSN?: string; SENTRY_ENVIRONMENT?: string},
  fetcher: typeof fetch = fetch,
): Promise<void> {
  if (!env.SENTRY_DSN) return;
  const target = parseDsn(env.SENTRY_DSN);
  if (!target) return;
  const eventId = globalThis.crypto.randomUUID().replaceAll("-", "");
  const type = error instanceof Error ? error.name : typeof error;
  const event = {
    event_id: eventId,
    timestamp: Date.now() / 1000,
    platform: "node",
    level: "error",
    environment: env.SENTRY_ENVIRONMENT ?? "production",
    // The message is left out on purpose: it can carry tokens, file names or transcript text.
    exception: {values: [{type, value: "server error (message not reported)"}]},
    tags: {route: (context.path ?? "").split("?", 1)[0], method: context.method ?? ""},
  };
  const body = [
    JSON.stringify({event_id: eventId, sent_at: new Date().toISOString()}),
    JSON.stringify({type: "event"}),
    JSON.stringify(event),
  ].join("\n");
  try {
    await fetcher(target.endpoint, {
      method: "POST",
      headers: {
        "content-type": "application/x-sentry-envelope",
        "x-sentry-auth": `Sentry sentry_version=7, sentry_key=${target.key}, sentry_client=video-studio-web/1`,
      },
      body,
      signal: AbortSignal.timeout(3000),
    });
  } catch {
    // Reporting must never break the request that failed.
  }
}
