export type AccessConfig = {
  mode: "tailscale" | "off";
  ownerLogin: string | null;
  allowedHosts: string[];
  allowedOrigins: string[];
};
export type Refusal = {status: number; code: string};
export type GuardRequest = {method?: string; url?: string; headers: Record<string, string | string[] | undefined>};

export const REQUEST_TIMEOUT_MS: number;
export const IDLE_TIMEOUT_MS: number;
export function loadConfig(env: Record<string, string | undefined>): AccessConfig;
export function checkRequest(request: GuardRequest, config: AccessConfig): Refusal | null;
export function refuse(res: unknown, refusal: Refusal): void;
export function patchCreateServer(httpModule: unknown, config: AccessConfig): void;
