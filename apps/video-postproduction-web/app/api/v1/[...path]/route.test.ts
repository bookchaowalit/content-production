// SPEC-001 F2: the route re-checks identity and same-origin before proxying (defence in depth behind the launcher).
import http from "node:http";
import type {AddressInfo} from "node:net";

import {afterAll, beforeAll, beforeEach, describe, expect, it} from "vitest";

import {GET, POST} from "./route";

let upstream: http.Server;
let base = "";
const owner = {host: "video-studio.tail.ts.net", "tailscale-user-login": "owner@example.com"};

beforeAll(async () => {
  upstream = http.createServer((_req, res) => {
    res.writeHead(200, {"content-type": "application/json"});
    res.end('{"ok":true}');
  });
  await new Promise<void>((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`;
});
afterAll(async () => {
  await new Promise<void>((resolve) => upstream.close(() => resolve()));
});
beforeEach(() => {
  process.env.STUDIO_ACCESS_MODE = "tailscale";
  process.env.STUDIO_OWNER_LOGIN = "owner@example.com";
  process.env.STUDIO_PUBLIC_HOST = "video-studio.tail.ts.net";
  process.env.VIDEO_POSTPRODUCTION_API_INTERNAL_URL = base;
});

const params = (path: string[]) => ({params: Promise.resolve({path})});

describe("api/v1 route", () => {
  it("refuses a request without the owner identity", async () => {
    const response = await GET(new Request("http://video-studio.tail.ts.net/api/v1/projects", {headers: {host: owner.host}}), params(["projects"]));
    expect(response.status).toBe(401);
  });

  it("refuses a cross-site write", async () => {
    const response = await POST(
      new Request("http://video-studio.tail.ts.net/api/v1/projects", {method: "POST", headers: {...owner, "sec-fetch-site": "cross-site"}}),
      params(["projects"]),
    );
    expect(response.status).toBe(403);
  });

  it("proxies an owner request", async () => {
    const response = await GET(new Request("http://video-studio.tail.ts.net/api/v1/projects", {headers: owner}), params(["projects"]));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ok: true});
  });
});
