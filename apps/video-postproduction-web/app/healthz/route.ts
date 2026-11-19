// SPEC-001 R-HEALTH: readiness for the heartbeat probe. No identity needed (the launcher still checks Host);
// the body never carries details.
import http from "node:http";

export const dynamic = "force-dynamic";

function apiReady(base: string): Promise<boolean> {
  return new Promise((resolve) => {
    const target = new URL("/readyz", base);
    const req = http.get(target, {timeout: 3000}, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("timeout", () => req.destroy());
    req.on("error", () => resolve(false));
  });
}

export async function GET(): Promise<Response> {
  const ok = await apiReady(process.env.VIDEO_POSTPRODUCTION_API_INTERNAL_URL ?? "http://api:8000");
  return Response.json({status: ok ? "ok" : "degraded"}, {status: ok ? 200 : 503, headers: {"cache-control": "no-store"}});
}
