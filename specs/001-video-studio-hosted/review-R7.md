# SPEC-001 review round R7 — Codex and the security-guidance plugin on the first P1 commits (2026-11-19)

Scope: `feat/video-studio-hosted` 3e24924..f5c42ca (code), `spec/video-studio-hosted` b2f126a..3e24924 (spec, re-run
after approval) and the throwaway `spike/video-studio-proxy` 7cb1c04. Each finding was checked against the code.

## Code (fixed on `feat/video-studio-hosted`, tests first)

| # | Sev | Finding | Verified | Fix |
|---|---|---|---|---|
| 1 | P1 | Retranscription jobs (transcribe only) could never be claimed: claim looked up `probe` | yes (`services.py:77` kind `retranscription`) | order applies to ingest only; test `test_retranscription_job_runs_without_a_probe_stage` |
| 2 | P1 | No heartbeat while a stage works; recovery took over live long stages after 180 s | yes | heartbeat thread with its own session, lease-guarded; lost lease stops the stage; test with a file DB |
| 3 | P1 | Recovery took over render stages that have no lease yet | yes (`RenderService.run_job`) | recovery limited to `ingest` / `retranscription`; test |
| 4 | P1 | Recovery wrote unconditionally and could overwrite a just-finished stage | yes | `take_over_stale_stage` conditional on status, token and heartbeat; test |
| 5 | P2 | Transcript rows flushed before the lease check | yes | guard right after the provider returns |
| 6 | P2 | Migration repair skipped retranscription jobs | yes | included; test seeds one |
| 7 | P2 | Body without the closing boundary accepted | yes (`finalize` does not check) | `on_end` tracked, `IncompleteBody`; test |
| 8 | P2 | Unbounded header buffers | partly — python-multipart 0.0.34 caps part headers (8 × 4224 B), but its error escaped as `MultipartParseError` | mapped to `InvalidMultipart`; test with a 10 KB header |
| 9 | P2 | Partial temp file kept if `close()` fails on a full disk | yes | unlink in `finally` |
| 10 | P2 | `migrate` failed on SQLite in a missing directory | yes | `_ensure_sqlite_parent` first; test |
| 11 | P2 | `python-multipart>=0.0.9` lacks the `python_multipart` namespace | yes | pin `>=0.0.34` |

## Spec (v1.1, no scope change)

| # | Sev | Finding | Disposition |
|---|---|---|---|
| 12 | P1 | `update.sh` must update `web` together with API and worker | accepted — R-UPGRADE |
| 13 | P1 | First heartbeat in the claim transaction | accepted — already in code; R-LEASE text |
| 14 | P1 | Force-stop the old worker before 0004 on every path | **disputed**: `update.sh` stops it, the desktop recovery action will too, and any path that skips it is healed by recovery rule (c) and the 0004 repairs (tested). A hard block would need the API to detect old workers, which they cannot report. |
| 15 | P2 | Render files must match the attempt in the dump | **deferred to F5**: reconcile already follows the dumped `render_outputs.status` and files are attempt-scoped (R-LEASE); covered by AC-21 when F5 is built |
| 16 | P2 | Pages need a 401 before Next renders | accepted — R-TS-5 checks move into the launcher |
| 17 | P2 | In-place render retry must reset the render row too | accepted — R-RERENDER |
| 18 | P2 | Upload criteria depend on the spike's mode | accepted — spike confirmed `Content-Length`; chunked fallback removed |

## Spike (`spike/video-studio-proxy`, not merged) and security-guidance plugin

The spike is throwaway by design (spec §6 step 1); its route has no identity or same-origin check because it only
measured streaming. **No code from it is merged.** The findings are carried into the real proxy as requirements:

| # | Finding | Where it lands |
|---|---|---|
| 19 | Server token attached without authenticating the caller (plugin HIGH, Codex P1) | already R-TS-3 / R-PROXY-6, now also enforced in the launcher (R-TS-5) |
| 20 | Same-origin proxy could serve active content (plugin MEDIUM) | R-PROXY-4 content-type allowlist + CSP sandbox |
| 21 | 2 h request timeout without an idle limit (plugin MEDIUM) | R-PROXY-9 inactivity timeout 10 min, behind identity |
| 22 | Abort upstream on client abort; close both on upstream timeout; `null` body for HEAD | R-PROXY-9 |
| 23 | uid and gid in the image | R-PROXY-9 |
