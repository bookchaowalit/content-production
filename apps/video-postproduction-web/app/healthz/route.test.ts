// SPEC-001 R-HEALTH: web /healthz follows the API /readyz and returns only a status.
import http from "node:http";
import type {AddressInfo} from "node:net";

import {afterEach, describe, expect, it} from "vitest";

import {GET} from "./route";

let server: http.Server | null = null;

async function api(status: number): Promise<string> {
  server = http.createServer((_req, res) => {
    res.writeHead(status);
    res.end();
  });
  await new Promise<void>((resolve) => server!.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}

afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
  server = null;
});

describe("healthz", () => {
  it("is ok when the API is ready", async () => {
    process.env.VIDEO_POSTPRODUCTION_API_INTERNAL_URL = await api(200);
    const response = await GET();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({status: "ok"});
  });

  it("is degraded when the API is not ready or unreachable", async () => {
    process.env.VIDEO_POSTPRODUCTION_API_INTERNAL_URL = await api(503);
    expect((await GET()).status).toBe(503);
    process.env.VIDEO_POSTPRODUCTION_API_INTERNAL_URL = "http://127.0.0.1:1";
    expect(await (await GET()).json()).toEqual({status: "degraded"});
  });
});
