# Runbook — hosted Video Studio (homelab, Tailscale only)

Spec: `specs/001-video-studio-hosted/spec.md` (SPEC-001). Host: Fedora `192.168.152.12`. Reachable only from owner
devices on the personal tailnet at `https://video-studio.<tailnet>.ts.net`.

## Layout on the host

| Path | What |
|---|---|
| `/srv/video-studio/src` | checkout of this repository at a release tag `studio-vX.Y.Z` |
| `/srv/video-studio/env/*.env` | per-service env files (mode 600), written by `scripts/studio/render_env.py` from Infisical `/video-studio` |
| `/srv/video-studio/run/STUDIO_TAG` | the running release |
| `/srv/video-studio/run/maintenance.lock` | shared by backup and retention |
| `/srv/video-studio/backup/studio.dump` | last `pg_dump` (also inside every restic snapshot) |

Commands below run as `bookchaowalit` from `/srv/video-studio/src`; `studio.sh` = `scripts/studio/studio.sh`.

## First install (P2, after the owner tailnet actions)

1. Owner: tailnet policy reviewed (no allow-all; grant owner → `tag:video-studio` tcp:443; `tagOwners`), MagicDNS
   and HTTPS on, one-use pre-authorised tagged auth key stored as `TS_AUTHKEY` in Infisical `/video-studio`, R2
   bucket token, Sentry DSNs. Save the policy file in the evidence.
2. `git clone` into `/srv/video-studio/src`, `git checkout refs/tags/studio-v0.1.0`.
3. `python3 scripts/studio/render_env.py --out /srv/video-studio/env` (prints file names only).
4. `scripts/studio/studio.sh update studio-v0.1.0` (builds, checks config, starts in order).
5. Install `deploy/systemd/video-studio*.{service,timer}` (not `video-studio-health.*`) into `/etc/systemd/system`,
   `systemctl enable --now video-studio.service video-studio-retention.timer video-studio-backup.timer
   video-studio-worker-ping.timer`.
6. On beelink: install `scripts/studio/health-probe.sh` as `/usr/local/bin/video-studio-health-probe`, put
   `STUDIO_URL` and `HEARTBEAT_TOKEN_FILE` in `/etc/video-studio/health.env`, enable `video-studio-health.timer`.
7. D3: recreate `ci-runner-local-2` with a 4 GB memory limit.
8. Check: `ss -ltnp` shows no studio port; `docker compose ... ps` shows no published port; from the owner laptop
   the studio opens; from a non-allowed tailnet device it does not.

## Start, stop, status

```bash
scripts/studio/studio.sh up
scripts/studio/studio.sh down
STUDIO_TAG=$(cat /srv/video-studio/run/STUDIO_TAG) STUDIO_ENV_DIR=/srv/video-studio/env \
  docker compose -f services/video-postproduction/docker-compose.yml -f services/video-postproduction/docker-compose.prod.yml ps
```

## Update to a release

```bash
scripts/studio/studio.sh update studio-vX.Y.Z
```

It fetches the tag, builds both images, runs `check_config.py`, **stops the old worker first** (R-UPGRADE), starts
the API (which migrates), then the worker and web. Migrations are additive; the API image tolerates a newer schema.

## Rollback

```bash
scripts/studio/studio.sh update studio-v<previous>
```

Never downgrade the database. The earliest valid target is `studio-v0.1.0`. Check `/healthz` over the tailnet and
open one project.

## Disk full

Retention reports `ok:false` under 20 % free (`video-studio-retention` heartbeat). Run
`docker compose ... run --rm api python -m video_postproduction.retention sweep --dry-run` to see what would go,
then the real sweep. Uploads refuse with 507 before reading the body when space is short.

## Host reboot

`video-studio.service` starts the stack; the tailscale node reuses `tailscale_state` (no new key). Check the
`video-studio-health` heartbeat within 10 min.

## Rotate secrets

- API token: new value in Infisical → `render_env.py` → `studio.sh up` (recreates api and web).
- Tailscale node: new one-use key in Infisical, `docker volume rm video-studio_tailscale_state` **only** if the node
  must be re-registered, then `render_env.py` and `studio.sh up`.

## Backup and restore

- Nightly 02:45 BKK: `studio.sh backup` (lock → `pg_dump` → `restic backup` of dump + storage without `tmp/` and
  attempt directories → `restic forget --prune` → heartbeat with repository size).
- Restore drill (monthly; first before release): restore a snapshot into a throwaway Compose project with no ports
  and no `tailscale` service, run the restore fix-up and reconcile (SPEC-001 R-BAK-3), then check one project's
  asset, transcript, EDL and render hash against `trace.json`. The fix-up/reconcile command is delivered with the
  drill (P3).
