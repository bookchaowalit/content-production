// SPEC-001 R-PROXY-1/3/4/5/9: same-origin streaming proxy to the API.
import http from "node:http";
import type {AddressInfo} from "node:net";

import {afterAll, beforeAll, describe, expect, it} from "vitest";

import {proxyRequest} from "./proxy";

type Seen = {method?: string; url?: string; headers: http.IncomingHttpHeaders; bytes: number};
let upstream: http.Server;
let base = "";
const seen: Seen[] = [];
const SENTINEL = "sentinel-token-0123456789abcdefXYZ";

beforeAll(async () => {
  upstream = http.createServer((req, res) => {
    const record: Seen = {method: req.method, url: req.url, headers: req.headers, bytes: 0};
    seen.push(record);
    req.on("data", (chunk: Buffer) => (record.bytes += chunk.length));
    req.on("end", () => {
      if (req.url?.startsWith("/api/v1/renders/r1/stream")) {
        const range = req.headers.range;
        res.writeHead(range ? 206 : 200, {
          "content-type": "video/mp4",
          ...(range ? {"content-range": "bytes 0-3/10"} : {}),
          "content-length": range ? "4" : "10",
          "accept-ranges": "bytes",
          "set-cookie": "leak=1",
          "x-powered-by": "secret",
        });
        res.end(range ? "abcd" : "abcdefghij");
        return;
      }
      if (req.url?.startsWith("/api/v1/jobs/j1/files/MANUAL-STEPS.md")) {
        res.writeHead(200, {"content-type": "text/markdown", "content-disposition": "inline"});
        res.end("<script>alert(1)</script>");
        return;
      }
      if (req.url?.startsWith("/api/v1/slow")) {
        res.writeHead(429, {"content-type": "application/json", "retry-after": "7", "cache-control": "max-age=60"});
        res.end("{}");
        return;
      }
      res.writeHead(200, {"content-type": "application/json"});
      res.end(JSON.stringify({ok: true, bytes: record.bytes}));
    });
  });
  await new Promise<void>((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => upstream.close(() => resolve()));
});

const env = (token?: string) => ({VIDEO_POSTPRODUCTION_API_INTERNAL_URL: base, VIDEO_EDITOR_API_TOKEN: token});

function request(method: string, url: string, init: RequestInit = {}): Request {
  return new Request(`http://studio.test${url}`, {method, ...init});
}

describe("proxyRequest", () => {
  it("adds the server token upstream and never returns it", async () => {
    seen.length = 0;
    const response = await proxyRequest(request("GET", "/api/v1/projects", {headers: {authorization: "Bearer browser"}}), ["projects"], env(SENTINEL));
    expect(response.status).toBe(200);
    expect(seen[0].headers.authorization).toBe(`Bearer ${SENTINEL}`);
    const text = await response.text();
    expect(text).not.toContain(SENTINEL);
    for (const value of response.headers.values()) expect(value).not.toContain(SENTINEL);
  });

  it("sends no Authorization header when no token is set", async () => {
    seen.length = 0;
    await proxyRequest(request("GET", "/api/v1/projects", {headers: {authorization: "Bearer browser"}}), ["projects"], env());
    expect(seen[0].headers.authorization).toBeUndefined();
  });

  it("streams a request body with its Content-Length", async () => {
    seen.length = 0;
    const payload = new Uint8Array(3 * 1024 * 1024).fill(7);
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(payload);
        controller.close();
      },
    });
    const response = await proxyRequest(
      request("POST", "/api/v1/projects/p/assets", {
        body,
        duplex: "half",
        headers: {"content-type": "multipart/form-data; boundary=x", "content-length": String(payload.length)},
      } as RequestInit),
      ["projects", "p", "assets"],
      env(),
    );
    expect(response.status).toBe(200);
    expect(seen[0].headers["content-length"]).toBe(String(payload.length));
    expect(seen[0].headers["transfer-encoding"]).toBeUndefined();
    expect(seen[0].bytes).toBe(payload.length);
  });

  it("passes Range through and filters response headers", async () => {
    const response = await proxyRequest(request("GET", "/api/v1/renders/r1/stream", {headers: {range: "bytes=0-3"}}), ["renders", "r1", "stream"], env());
    expect(response.status).toBe(206);
    expect(response.headers.get("content-range")).toBe("bytes 0-3/10");
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("x-powered-by")).toBeNull();
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(response.headers.get("content-security-policy")).toBe("sandbox; default-src 'none'");
    expect(await response.text()).toBe("abcd");
  });

  it("answers HEAD with headers only", async () => {
    const response = await proxyRequest(request("HEAD", "/api/v1/renders/r1/stream"), ["renders", "r1", "stream"], env());
    expect(response.status).toBe(200);
    expect(response.headers.get("content-length")).toBe("10");
    expect(response.body).toBeNull();
  });

  it("downgrades content types that could run in the studio origin", async () => {
    const response = await proxyRequest(request("GET", "/api/v1/jobs/j1/files/MANUAL-STEPS.md"), ["jobs", "j1", "files", "MANUAL-STEPS.md"], env());
    expect(response.headers.get("content-type")).toBe("application/octet-stream");
    expect(response.headers.get("content-disposition")).toBe("attachment");
  });

  it("keeps Retry-After and overrides upstream cache headers", async () => {
    const response = await proxyRequest(request("POST", "/api/v1/slow"), ["slow"], env());
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("7");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });

  it("re-serialises the query string", async () => {
    seen.length = 0;
    await proxyRequest(request("GET", "/api/v1/projects?limit=5&q=a%20b"), ["projects"], env());
    expect(seen[0].url).toBe("/api/v1/projects?limit=5&q=a+b");
  });

  it("refuses bad segments without calling upstream", async () => {
    seen.length = 0;
    for (const segments of [[".."], ["."], ["a/b"], ["a\\b"], ["a\u0000b"], [""]]) {
      const response = await proxyRequest(request("GET", "/api/v1/x"), segments, env());
      expect(response.status).toBe(400);
    }
    expect(seen).toEqual([]);
  });

  it("answers 502 when the API is unreachable", async () => {
    const response = await proxyRequest(request("GET", "/api/v1/projects"), ["projects"], {
      VIDEO_POSTPRODUCTION_API_INTERNAL_URL: "http://127.0.0.1:1",
    });
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({error: {code: "upstream_unavailable"}});
  });

  it("cancels the upstream request when the client aborts", async () => {
    const controller = new AbortController();
    const body = new ReadableStream({
      pull() {
        return new Promise(() => {}); // never ends, like a stalled upload
      },
    });
    const pending = proxyRequest(
      new Request("http://studio.test/api/v1/projects/p/assets", {method: "POST", body, duplex: "half", signal: controller.signal} as RequestInit),
      ["projects", "p", "assets"],
      env(),
    );
    setTimeout(() => controller.abort(), 50);
    const response = await pending;
    expect([499, 502]).toContain(response.status);
  });
});
