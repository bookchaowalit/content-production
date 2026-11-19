#!/usr/bin/env python3
"""Write the per-service env files of the hosted Video Studio from Infisical (SPEC-001 R-COMPOSE-2, R-HOST).

    python3 scripts/studio/render_env.py --out /srv/video-studio/env

Reads `infisical export --path /video-studio --format json` (the CLI must be logged in) and writes one mode-600
file per service with only the keys that service needs. Never prints values; prints only the file names written
and the key names missing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# service file -> keys it receives (secret or not); DATABASE_URL and STUDIO_HEALTH_HOST are derived below.
LAYOUT: dict[str, list[str]] = {
    "postgres": ["POSTGRES_PASSWORD"],
    "api": ["VIDEO_EDITOR_API_TOKEN", "DATABASE_URL", "SENTRY_DSN"],
    "worker": ["DATABASE_URL", "SENTRY_DSN"],
    "web": ["VIDEO_EDITOR_API_TOKEN", "STUDIO_OWNER_LOGIN", "STUDIO_PUBLIC_HOST", "STUDIO_HEALTH_HOST", "SENTRY_DSN"],
    "tailscale": ["TS_AUTHKEY"],
    "backup": ["R2_BACKUP_ACCESS_KEY_ID", "R2_BACKUP_SECRET_ACCESS_KEY", "R2_BACKUP_ENDPOINT", "RESTIC_PASSWORD",
               "RESTIC_REPOSITORY", "HEARTBEAT_TOKEN", "POSTGRES_PASSWORD"],
}
SENTRY_BY_SERVICE = {"api": "SENTRY_DSN_API", "worker": "SENTRY_DSN_API", "web": "SENTRY_DSN_WEB"}
REQUIRED = {"POSTGRES_PASSWORD", "VIDEO_EDITOR_API_TOKEN", "STUDIO_OWNER_LOGIN", "STUDIO_PUBLIC_HOST", "TS_AUTHKEY"}


def load_secrets(path: str) -> dict[str, str]:
    result = subprocess.run(
        ["infisical", "export", "--path", path, "--format", "json"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print(f"infisical export failed (exit {result.returncode}); is the CLI logged in?", file=sys.stderr)
        sys.exit(2)
    return {item["key"]: item["value"] for item in json.loads(result.stdout)}


def derive(secrets: dict[str, str]) -> dict[str, str]:
    values = dict(secrets)
    if "POSTGRES_PASSWORD" in values:
        values["DATABASE_URL"] = (
            f"postgresql+psycopg://video:{values['POSTGRES_PASSWORD']}@postgres:5432/video_postproduction"
        )
    if "STUDIO_PUBLIC_HOST" in values:
        values["STUDIO_HEALTH_HOST"] = values["STUDIO_PUBLIC_HOST"]
    return values


def write(out: Path, values: dict[str, str]) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    missing: set[str] = set()
    for service, keys in LAYOUT.items():
        lines = []
        for key in keys:
            source = SENTRY_BY_SERVICE.get(service) if key == "SENTRY_DSN" else key
            value = values.get(source or key)
            if value is None:
                missing.add(source or key)
                continue
            if "\n" in value:
                raise SystemExit(f"{key} contains a newline; refusing to write it")
            lines.append(f"{key}={value}")
        target = out / f"{service}.env"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        os.chmod(target, 0o600)
        print(f"wrote {target.name}")
    return sorted(missing)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("/srv/video-studio/env"))
    parser.add_argument("--infisical-path", default="/video-studio")
    args = parser.parse_args()
    missing = write(args.out, derive(load_secrets(args.infisical_path)))
    if missing:
        print("missing keys: " + ", ".join(missing))
    return 1 if REQUIRED & set(missing) else 0


if __name__ == "__main__":
    sys.exit(main())
