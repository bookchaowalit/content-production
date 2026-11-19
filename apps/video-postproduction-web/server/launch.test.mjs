// SPEC-001 R-PROXY-2 / R-TS-5: the launcher validates settings, guards the server, then starts Next.
import {describe, expect, it} from "vitest";

import {launch} from "./launch.mjs";

function fakeHttp() {
  const created = [];
  return {
    created,
    createServer(listener) {
      const server = {listener};
      created.push(server);
      return server;
    },
  };
}

describe("launch", () => {
  it("refuses to start with bad settings and never loads Next", async () => {
    const errors = [];
    let loaded = false;
    const code = await launch({
      env: {},
      http: fakeHttp(),
      importServer: async () => {
        loaded = true;
      },
      log: {error: (m) => errors.push(m), warn: () => {}},
    });
    expect(code).toBe(1);
    expect(loaded).toBe(false);
    expect(errors[0]).toMatch(/refuses to start/);
  });

  it("patches createServer before loading Next, so Next's server is guarded", async () => {
    const http = fakeHttp();
    const warnings = [];
    const code = await launch({
      env: {STUDIO_ACCESS_MODE: "off"},
      http,
      importServer: async () => {
        http.createServer(() => {});
      },
      log: {error: () => {}, warn: (m) => warnings.push(m)},
    });
    expect(code).toBe(0);
    expect(http.created[0].requestTimeout).toBe(2 * 60 * 60 * 1000);
    expect(http.created[0].timeout).toBe(10 * 60 * 1000);
    expect(warnings[0]).toMatch(/off/);
    const responses = [];
    http.created[0].listener({method: "GET", url: "/", headers: {host: "evil"}}, {
      writeHead: (status) => responses.push(status),
      end: () => {},
    });
    expect(responses).toEqual([421]);
  });
});
