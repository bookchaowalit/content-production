// Request checks at the HTTP entry, before Next renders anything (SPEC-001 R-TS-3/4/5, R-PROXY-5/6).
// Plain ESM so the standalone launcher (server-launch.mjs) can load it without the Next build.

const RAW_PATH_BAD = /(\.\.|%2e|%2f|%5c|%00|\\)/i;
const DEFAULT_LOCAL_HOSTS = ["127.0.0.1:3400", "localhost:3400"];
const SAFE_METHODS = new Set(["GET", "HEAD"]);
export const REQUEST_TIMEOUT_MS = 2 * 60 * 60 * 1000;
export const IDLE_TIMEOUT_MS = 10 * 60 * 1000;

function list(value) {
  return (value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

/** Read and validate the access settings; throws when they are missing or unknown (fail closed). */
export function loadConfig(env) {
  const mode = env.STUDIO_ACCESS_MODE;
  if (mode === "tailscale") {
    if (!env.STUDIO_OWNER_LOGIN) throw new Error("STUDIO_ACCESS_MODE=tailscale needs STUDIO_OWNER_LOGIN");
    if (!env.STUDIO_PUBLIC_HOST) throw new Error("STUDIO_ACCESS_MODE=tailscale needs STUDIO_PUBLIC_HOST");
    const host = env.STUDIO_PUBLIC_HOST.toLowerCase();
    return {
      mode,
      ownerLogin: env.STUDIO_OWNER_LOGIN.toLowerCase(),
      allowedHosts: [host],
      allowedOrigins: [`https://${host}`],
    };
  }
  if (mode === "off") {
    const hosts = list(env.STUDIO_LOCAL_HOSTS);
    const allowedHosts = (hosts.length ? hosts : DEFAULT_LOCAL_HOSTS).map((h) => h.toLowerCase());
    return {mode, ownerLogin: null, allowedHosts, allowedOrigins: allowedHosts.map((h) => `http://${h}`)};
  }
  throw new Error("STUDIO_ACCESS_MODE must be 'tailscale' or 'off'");
}

function header(headers, name) {
  const value = headers[name];
  return Array.isArray(value) ? value[0] : value;
}

/** null when the request may continue, otherwise {status, code}. */
export function checkRequest(request, config) {
  const url = request.url ?? "/";
  if (url.startsWith("/api/") && RAW_PATH_BAD.test(url)) return {status: 400, code: "invalid_path"};

  const host = (header(request.headers, "host") ?? "").toLowerCase();
  if (!config.allowedHosts.includes(host)) return {status: 421, code: "wrong_host"};

  const path = url.split("?", 1)[0];
  if (path === "/healthz") return null;

  if (config.mode === "tailscale") {
    const login = (header(request.headers, "tailscale-user-login") ?? "").toLowerCase();
    if (!login || login !== config.ownerLogin) return {status: 401, code: "access_required"};
  }

  if (!SAFE_METHODS.has((request.method ?? "GET").toUpperCase())) {
    const site = header(request.headers, "sec-fetch-site");
    if (site !== undefined) {
      if (site !== "same-origin") return {status: 403, code: "cross_origin_refused"};
    } else {
      const origin = (header(request.headers, "origin") ?? "").toLowerCase();
      if (!config.allowedOrigins.includes(origin)) return {status: 403, code: "cross_origin_refused"};
    }
  }
  return null;
}

export function refuse(res, refusal) {
  const body = JSON.stringify({error: {code: refusal.code}});
  res.writeHead(refusal.status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
    "cache-control": "private, no-store",
  });
  res.end(body);
}

/** Wrap http.createServer so every request passes checkRequest first, with long request and short idle timeouts. */
export function patchCreateServer(httpModule, config) {
  const original = httpModule.createServer;
  httpModule.createServer = function patched(...args) {
    const index = args.findIndex((arg) => typeof arg === "function");
    if (index >= 0) {
      const listener = args[index];
      args[index] = function guarded(req, res) {
        const refusal = checkRequest(req, config);
        if (refusal) {
          refuse(res, refusal);
          return;
        }
        return listener.call(this, req, res);
      };
    }
    const server = original.apply(this, args);
    server.requestTimeout = REQUEST_TIMEOUT_MS;
    server.timeout = IDLE_TIMEOUT_MS;
    return server;
  };
}
