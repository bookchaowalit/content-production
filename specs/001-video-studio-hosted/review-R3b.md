# SPEC-001 review round R3b — Codex on commit 5f6708a (2026-11-18)

Reviewer: Codex (`gpt-6.2-sol`, Stop hook) on `b2f126a..5f6708a` (spec v0.4). Each finding was checked against the
code at b2f126a. Outcome: **revise** → v0.5.

| # | Sev | Finding | Verified | Change in v0.5 |
|---|---|---|---|---|
| 1 | P1 | Restore reconcile would delete exports, which have no rows | yes (`exports.py:81-83,120-144`) | reconcile by kind; exports always kept; fact 20; AC-21 |
| 2 | P1 | Takeover leaves the old token valid until the next `complete` | yes (v0.4 text) | token cleared in the same update; guard on status + token for success and failure; AC-11 |
| 3 | P1 | Recovery could send `transcribe` before `probe` (both `queued` at ingest) | yes (`services.py:183-190`) | `blocked` state, migration of old rows, claim checks previous stage; AC-10 |
| 4 | P1 | A stage stuck `running` after a worker stop is never recovered; `recover_stale_jobs` has no caller | yes (no caller; lease from `job.started_at`) | R-LEASE stage heartbeat + periodic `recover_jobs`; fact 19; AC-10 |
| 5 | P2 | Chunks of `completed` sessions can stay on disk | yes | cleanup at API start and in the sweep; AC-10 |
| 6 | P2 | Final stage and job finish in two commits | yes (`stages.py:308-312`) | one commit; AC-10 |
| 7 | P2 | Render reconcile by id keeps history of unfinished renders | yes (`render/service.py:261-268,353-361`) | reconcile by dumped status; AC-21 |
| 8 | P2 | `assembling` past `expires_at` dropped from the reservation | yes (v0.4 text) | counts until it ends; AC-11 |
| 9 | P2 | Resume with same metadata but different bytes fails the session | yes | per-chunk digests compared before sending; AC-13 |
| 10 | P2 | `instrumentation.ts` missing from the desktop bundle | yes (`runtime-bundle-manifest.js`) | added to `REQUIRED_FILES`; AC-24 |
| 11 | P2 | `require-tests` not an input of `ci-python.yml`; vitest writes no coverage summary | yes (`ci.yml:163,213`) | R-CI-4 rewritten; AC-27 |
| 12 | P2 | API token given to the worker | yes (`dependencies.py:87-103` is the only use) | token only in api + web; AC-26 |

Disputed: none.
