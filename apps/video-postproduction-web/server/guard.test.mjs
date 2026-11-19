// SPEC-001 R-TS-3/4/5, R-PROXY-5/6: request checks run at the HTTP entry, before Next.
import {describe, expect, it} from "vitest";

import {checkRequest, loadConfig, patchCreateServer} from "./guard.mjs";

const PROD = {
  STUDIO_ACCESS_MODE: "tailscale",
  STUDIO_OWNER_LOGIN: "Owner@Example.com",
  STUDIO_PUBLIC_HOST: "video-studio.tail1234.ts.net",
};
const prod = loadConfig(PROD);
const local = loadConfig({STUDIO_ACCESS_MODE: "off"});
const owner = {"tailscale-user-login": "owner@example.com", host: "video-studio.tail1234.ts.net"};

const req = (method, url, headers) => ({method, url, headers});

describe("loadConfig", () => {
  it("refuses an unset or unknown mode", () => {
    expect(() => loadConfig({})).toThrow(/STUDIO_ACCESS_MODE/);
    expect(() => loadConfig({STUDIO_ACCESS_MODE: "open"})).toThrow(/STUDIO_ACCESS_MODE/);
  });
  it("refuses tailscale mode without owner or host", () => {
    expect(() => loadConfig({STUDIO_ACCESS_MODE: "tailscale", STUDIO_PUBLIC_HOST: "h"})).toThrow(/STUDIO_OWNER_LOGIN/);
    expect(() => loadConfig({STUDIO_ACCESS_MODE: "tailscale", STUDIO_OWNER_LOGIN: "o"})).toThrow(/STUDIO_PUBLIC_HOST/);
  });
  it("uses the default local hosts and accepts an override", () => {
    expect(local.allowedHosts).toEqual(["127.0.0.1:3400", "localhost:3400"]);
    expect(loadConfig({STUDIO_ACCESS_MODE: "off", STUDIO_LOCAL_HOSTS: "127.0.0.1:13400"}).allowedHosts).toEqual([
      "127.0.0.1:13400",
    ]);
  });
});

describe("checkRequest in tailscale mode", () => {
  it("lets the owner through", () => {
    expect(checkRequest(req("GET", "/", owner), prod)).toBeNull();
  });
  it("refuses a missing login, another login, and a tagged device (no user header)", () => {
    expect(checkRequest(req("GET", "/", {host: owner.host}), prod)).toMatchObject({status: 401, code: "access_required"});
    expect(
      checkRequest(req("GET", "/", {...owner, "tailscale-user-login": "someone@example.com"}), prod),
    ).toMatchObject({status: 401});
  });
  it("refuses a wrong Host before anything else", () => {
    expect(checkRequest(req("GET", "/", {...owner, host: "evil.example"}), prod)).toMatchObject({status: 421, code: "wrong_host"});
  });
  it("lets /healthz through without identity but still checks the Host", () => {
    expect(checkRequest(req("GET", "/healthz", {host: owner.host}), prod)).toBeNull();
    expect(checkRequest(req("GET", "/healthz", {host: "evil.example"}), prod)).toMatchObject({status: 421});
  });
  it("refuses cross-site writes and writes without provenance", () => {
    const write = (headers) => checkRequest(req("POST", "/api/v1/projects", {...owner, ...headers}), prod);
    expect(write({"sec-fetch-site": "same-origin"})).toBeNull();
    expect(write({"sec-fetch-site": "same-site"})).toMatchObject({status: 403, code: "cross_origin_refused"});
    expect(write({"sec-fetch-site": "cross-site"})).toMatchObject({status: 403});
    expect(write({origin: "https://video-studio.tail1234.ts.net"})).toBeNull();
    expect(write({origin: "https://evil.example"})).toMatchObject({status: 403});
    expect(write({})).toMatchObject({status: 403});
  });
  it("does not apply the write check to reads", () => {
    expect(checkRequest(req("GET", "/api/v1/projects", owner), prod)).toBeNull();
    expect(checkRequest(req("HEAD", "/api/v1/renders/x/stream", owner), prod)).toBeNull();
  });
});

describe("checkRequest raw path guard", () => {
  for (const url of ["/api/v1/../etc", "/api/v1/%2e%2e/x", "/api/v1/a%2Fb", "/api/v1/a%5cb", "/api/v1/a%00b", "/api/v1/a\\b"]) {
    it(`refuses ${url}`, () => {
      expect(checkRequest(req("GET", url, owner), prod)).toMatchObject({status: 400, code: "invalid_path"});
    });
  }
});

describe("checkRequest in off mode", () => {
  it("needs no identity and still refuses cross-site writes", () => {
    const host = {host: "127.0.0.1:3400"};
    expect(checkRequest(req("GET", "/", host), local)).toBeNull();
    expect(checkRequest(req("POST", "/api/v1/projects", {...host, origin: "http://127.0.0.1:3400"}), local)).toBeNull();
    expect(checkRequest(req("POST", "/api/v1/projects", {...host, "sec-fetch-site": "cross-site"}), local)).toMatchObject({
      status: 403,
    });
  });
});

describe("patchCreateServer", () => {
  it("wraps the request listener with the guard and sets long request / short idle timeouts", () => {
    const created = [];
    const fakeHttp = {
      createServer(listener) {
        const server = {listener};
        created.push(server);
        return server;
      },
    };
    patchCreateServer(fakeHttp, prod);
    const seen = [];
    const server = fakeHttp.createServer((request) => seen.push(request.url));
    expect(server.requestTimeout).toBe(2 * 60 * 60 * 1000);
    expect(server.timeout).toBe(10 * 60 * 1000);

    const responses = [];
    const res = {writeHead: (status, headers) => responses.push({status, headers}), end: () => {}};
    server.listener({method: "GET", url: "/", headers: {host: owner.host}}, res);
    expect(responses[0].status).toBe(401);
    expect(seen).toEqual([]);
    server.listener({method: "GET", url: "/", headers: owner}, res);
    expect(seen).toEqual(["/"]);
  });
});
