# SPEC-001 review round R3a — Codex on commit 8f7ff36 (2026-11-18)

Reviewer: Codex (`gpt-6.2-sol`, Stop hook) on `b2f126a..8f7ff36` (spec v0.3). Each finding was checked against the
code at b2f126a. Outcome: **revise** → v0.4. The Claude reviewer's R3 pass on v0.4 is still to run.

| # | Sev | Finding | Verified | Change in v0.4 |
|---|---|---|---|---|
| 1 | P1 | Crash between `probe` finishing and `transcribe` being enqueued is not recovered | yes (`stages.py` `run_probe` commit then enqueue) | `processing_stages.enqueued_at`, same-commit hand-off, `recover_unqueued_stages`, fact 17, AC-10 |
| 2 | P1 | Restore sets running stages to `retry_queued`, which the claim never picks up | yes (`recovery.py` sets stages `queued`) | fix-up: job `retry_queued`, stage `queued`, AC-21 |
| 3 | P2 | Files written after the dump restore without rows | yes (`render/service.py` writes then commits) | dump-before-snapshot invariant + reconcile + no-other-deleter test, AC-21 |
| 4 | P2 | New worker can start before the API migrates | yes (`docker-compose.yml` worker depends only on db/redis) | R-SCHEMA-WAIT, AC-30 |
| 5 | P2 | Client never re-sends `complete` after an interrupted assembly | yes | R-UP-9 re-complete, AC-11 |
| 6 | P2 | FFprobe refusal leaves the session undefined | yes (`services.py` probe error path) | `failed`/`asset_not_readable`, AC-10 |
| 7 | P2 | DELETE can race a successful assembly and return budget for a completed upload | yes | conditional abort, 409 on assembling/completed, AC-11 |
| 8 | P2 | Multipart route bypasses the disk reservation on hosted | yes | R-UP-MODE (`chunked_only` in prod), AC-29 |
| 9 | P2 | No way to switch an existing `accurate` project to `fast` | yes (`services.py` only fast→accurate) | `PATCH /projects/{id}`, fact 18, AC-16 |
| 10 | P2 | `npm run dev` breaks under the new start-up checks | yes (DOCPACK runbook) | committed `.env.development`, AC-31 |
| 11 | P3 | Render is one file, not two (R2 #8 was wrong) | yes (`render/service.py:171-172,262`) | R-RET-2 / AC-18 corrected, fact 13 rewritten |

Disputed: none.
