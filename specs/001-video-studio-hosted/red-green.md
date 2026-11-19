# SPEC-001 red/green log

ADR-0004: each behaviour is written test-first; the red run is recorded before the implementation.

## Migration 0004 (R-SIZE, R-QUEUE / R-LEASE repairs, R-RET-1) — 2026-11-19

- Test: `services/video-postproduction-api/tests/test_migration_0004.py` (SQLite always; Postgres when
  `VIDEO_TEST_POSTGRES_URL` is set).
- **Red** (before `0004_hosted_studio.py` existed): `test_0004_adds_columns_and_repairs_old_rows[sqlite]` failed —
  `size_bytes` reflected as `INTEGER`, not `BigInteger`. (The other two passed trivially because head was 0003.)
- **Green**: migration `0004_hosted_studio` + ORM columns. 6/6 pass on SQLite and on `postgres:16-alpine`
  (throwaway container), including storing 2,147,483,648 in `media_assets.size_bytes` on Postgres and the
  downgrade/upgrade round trip on an empty database. Full API suite still green.
