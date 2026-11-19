# SPEC-001 review round R11 — Codex on 3e24924..2e9d8a1 (2026-11-19)

Each finding was checked against the code.

| # | Sev | Finding | Verified | Fix |
|---|---|---|---|---|
| 1 | P1 | Desktop recover ran `stop --timeout 1800 worker` under the 30 s CLI timeout, so the warm shutdown was cut short | yes (`runCompose` always passed `commandTimeoutMs`) | `runCompose` takes a per-call timeout; the worker stop gets 1800 s + 120 s |
| 2 | P2 | `render_env.py` wrote files one by one and only then reported missing required keys, leaving a half-new env dir | yes | all files are rendered in memory first; missing required keys or a bad value write nothing; each file is replaced atomically (temp + fsync + rename, mode 600) |
| 3 | P2 | A value ending in `\` breaks Compose's single-quoted parsing | yes | backslashes are refused in Compose env files (`backup.env` stays literal) |
| 4 | P2 | Retention could delete the temp file of an open upload that stalled for over an hour (direct API client, no web idle timeout) | yes, for a client that reaches the API directly; the hosted path is cut by the launcher's 10 min idle timeout | the upload holds a shared `flock` on its temp file until the handle closes; the sweep skips any temp file it cannot lock exclusively (same kernel, shared volume) — replaces the R8 dispute |
| 5 | P2 | Render retry used its own default cap (3) instead of `MAX_STAGE_ATTEMPTS` | yes | API dependency and worker build `RenderService(max_attempts=settings.max_stage_attempts)` |
| 6 | P2 | A failed `pg_dump` ended `studio.sh backup` via `set -e` without a heartbeat | yes (fake `docker` exit 1: no POST) | dump and move are checked; failure removes the partial dump, sends `ok: false`, exits 1 |
| 7 | P3 | The render trace took quality from the project's current setting, not from the transcript the captions use | yes | `transcripts.quality` (nullable, added in the unreleased 0004) is set by the transcribe stage from the quality its provider was built for; the trace reads it (None for older rows) |

Disputed: none.
