// SPEC-001 R-OBS-3: web server errors reach Sentry errors-only, with no request data and no query strings.
import {describe, expect, it} from "vitest";

import {reportServerError} from "./report-error";

const DSN = "https://publickey123@o1.ingest.sentry.io/4242";

function capture() {
  const calls: {url: string; init: RequestInit}[] = [];
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({url: String(input), init: init ?? {}});
    return new Response("{}", {status: 200});
  }) as typeof fetch;
  return {calls, fetcher};
}

describe("reportServerError", () => {
  it("sends one envelope with the error type and the path only", async () => {
    const {calls, fetcher} = capture();
    await reportServerError(
      new TypeError("upstream failed for token=abc"),
      {path: "/api/v1/projects?secret=1", method: "POST"},
      {SENTRY_DSN: DSN},
      fetcher,
    );
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("https://o1.ingest.sentry.io/api/4242/envelope/");
    const auth = new Headers(calls[0].init.headers).get("x-sentry-auth") ?? "";
    expect(auth).toContain("sentry_key=publickey123");
    const body = String(calls[0].init.body);
    expect(body).toContain('"type":"TypeError"');
    expect(body).toContain('"route":"/api/v1/projects"');
    expect(body).not.toContain("secret=1");
    expect(body).not.toContain("token=abc");
    expect(body).not.toContain('"request"');
  });

  it("does nothing without a DSN and never throws", async () => {
    const {calls, fetcher} = capture();
    await reportServerError(new Error("x"), {path: "/"}, {}, fetcher);
    expect(calls).toHaveLength(0);
    const failing = (async () => {
      throw new Error("network");
    }) as typeof fetch;
    await expect(reportServerError(new Error("x"), {path: "/"}, {SENTRY_DSN: DSN}, failing)).resolves.toBeUndefined();
    await expect(reportServerError(new Error("x"), {path: "/"}, {SENTRY_DSN: "not a dsn"}, fetcher)).resolves.toBeUndefined();
  });
});
