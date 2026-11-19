// Same-origin streaming proxy to the Video API (SPEC-001 R-PROXY-1/3/4/5/9). Server-side only.
//
// node:http instead of fetch: it keeps Content-Length on a streamed body and has no 300 s body timeout
// (spike results in specs/001-video-studio-hosted/red-green.md).
import http from "node:http";
import {Readable} from "node:stream";
import type {ReadableStream as NodeReadableStream} from "node:stream/web";

const SEGMENT = /^[A-Za-z0-9_.-]+$/;
const FORWARD_REQUEST = ["content-type", "content-length", "range", "if-range", "if-none-match", "accept"];
const FORWARD_RESPONSE = [
  "content-type",
  "content-length",
  "content-range",
  "accept-ranges",
  "content-disposition",
  "etag",
  "retry-after",
  "x-request-id",
];
const SAFE_CONTENT_TYPES = [/^application\/json\b/i, /^video\//i, /^audio\//i, /^image\/(png|jpeg)\b/i, /^text\/plain\b/i];
export const UPSTREAM_TIMEOUT_MS = 2 * 60 * 60 * 1000;

export type ProxyEnv = {
  VIDEO_POSTPRODUCTION_API_INTERNAL_URL?: string;
  VIDEO_EDITOR_API_TOKEN?: string;
};

function json(status: number, code: string): Response {
  return Response.json({error: {code}}, {status, headers: {"cache-control": "private, no-store"}});
}

function validSegments(segments: string[]): boolean {
  return segments.length > 0 && segments.every((s) => s !== "." && s !== ".." && SEGMENT.test(s));
}

function responseHeaders(upstream: http.IncomingMessage): Headers {
  const out = new Headers();
  for (const name of FORWARD_RESPONSE) {
    const value = upstream.headers[name];
    if (typeof value === "string") out.set(name, value);
  }
  const type = out.get("content-type") ?? "";
  if (type && !SAFE_CONTENT_TYPES.some((pattern) => pattern.test(type))) {
    out.set("content-type", "application/octet-stream");
    out.set("content-disposition", "attachment");
  }
  out.set("cache-control", "private, no-store");
  out.set("x-content-type-options", "nosniff");
  out.set("content-security-policy", "sandbox; default-src 'none'");
  return out;
}

export async function proxyRequest(request: Request, segments: string[], env: ProxyEnv): Promise<Response> {
  if (!validSegments(segments)) return json(400, "invalid_path");
  const upstreamBase = new URL(env.VIDEO_POSTPRODUCTION_API_INTERNAL_URL ?? "http://api:8000");
  const incoming = new URL(request.url);
  const query = new URLSearchParams(incoming.search).toString();
  const path = `/api/v1/${segments.map(encodeURIComponent).join("/")}${query ? `?${query}` : ""}`;

  const headers: Record<string, string> = {};
  for (const name of FORWARD_REQUEST) {
    const value = request.headers.get(name);
    if (value !== null) headers[name] = value;
  }
  if (env.VIDEO_EDITOR_API_TOKEN) headers.authorization = `Bearer ${env.VIDEO_EDITOR_API_TOKEN}`;
  const isHead = request.method === "HEAD";
  const method = isHead ? "GET" : request.method;
  const hasBody = request.body !== null && method !== "GET";

  return await new Promise<Response>((resolve) => {
    let settled = false;
    const settle = (response: Response) => {
      if (!settled) {
        settled = true;
        resolve(response);
      }
    };
    const upstream = http.request(
      {
        protocol: upstreamBase.protocol,
        hostname: upstreamBase.hostname,
        port: upstreamBase.port || 80,
        path,
        method,
        headers,
      },
      (response) => {
        const out = responseHeaders(response);
        const status = response.statusCode ?? 502;
        if (isHead) {
          response.destroy();
          settle(new Response(null, {status, headers: out}));
          return;
        }
        response.on("error", () => upstream.destroy());
        settle(new Response(Readable.toWeb(response) as ReadableStream, {status, headers: out}));
      },
    );
    upstream.setTimeout(UPSTREAM_TIMEOUT_MS, () => upstream.destroy(new Error("upstream timeout")));
    upstream.on("error", () => settle(json(502, "upstream_unavailable")));
    request.signal.addEventListener("abort", () => {
      upstream.destroy();
      settle(json(499, "client_closed_request"));
    });

    if (hasBody) {
      const source = Readable.fromWeb(request.body as unknown as NodeReadableStream);
      source.on("error", () => upstream.destroy());
      source.pipe(upstream);
    } else {
      upstream.end();
    }
  });
}
