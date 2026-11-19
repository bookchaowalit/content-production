// Same-origin proxy to the Video API (SPEC-001 F2). The launcher already checked the request at the HTTP entry
// (R-TS-5); identity and same-origin are checked again here as defence in depth.
import {checkRequest, loadConfig} from "@/server/guard.mjs";
import {proxyRequest} from "@/lib/server/proxy";

export const dynamic = "force-dynamic";

type Context = {params: Promise<{path: string[]}>};

async function handle(request: Request, context: Context): Promise<Response> {
  const config = loadConfig(process.env);
  const url = new URL(request.url);
  const refusal = checkRequest(
    {method: request.method, url: url.pathname + url.search, headers: Object.fromEntries(request.headers)},
    config,
  );
  if (refusal) {
    return Response.json({error: {code: refusal.code}}, {status: refusal.status, headers: {"cache-control": "private, no-store"}});
  }
  const {path} = await context.params;
  return proxyRequest(request, path, {
    VIDEO_POSTPRODUCTION_API_INTERNAL_URL: process.env.VIDEO_POSTPRODUCTION_API_INTERNAL_URL,
    VIDEO_EDITOR_API_TOKEN: process.env.VIDEO_EDITOR_API_TOKEN,
  });
}

export const GET = handle;
export const HEAD = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
