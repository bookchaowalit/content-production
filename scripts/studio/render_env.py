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
from urllib.parse import quote

# service file -> keys it receives (secret or not); DATABASE_URL and STUDIO_HEALTH_HOST are derived below.
LAYOUT: dict[str, list[str]] = {
    "postgres": ["POSTGRES_PASSWORD"],
    "api": ["VIDEO_EDITOR_API_TOKEN", "DATABASE_URL", "SENTRY_DSN"],
    "worker": ["DATABASE_URL", "SENTRY_DSN"],
    "web": ["VIDEO_EDITOR_API_TOKEN", "STUDIO_OWNER_LOGIN", "STUDIO_PUBLIC_HOST", "STUDIO_HEALTH_HOST", "SENTRY_DSN"],
    "tailscale": ["TS_AUTHKEY"],
    # Read by `docker run --env-file` (no shell parsing) for the restic container, and by the heartbeat helper.
    "backup": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "RESTIC_PASSWORD", "RESTIC_REPOSITORY", "HEARTBEAT_TOKEN"],
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
            f"postgresql+psycopg://video:{quote(values['POSTGRES_PASSWORD'], safe='')}@postgres:5432/video_postproduction"
        )
    if "STUDIO_PUBLIC_HOST" in values:
        values["STUDIO_HEALTH_HOST"] = values["STUDIO_PUBLIC_HOST"]
    if "R2_BACKUP_ACCESS_KEY_ID" in values:
        values["AWS_ACCESS_KEY_ID"] = values["R2_BACKUP_ACCESS_KEY_ID"]
    if "R2_BACKUP_SECRET_ACCESS_KEY" in values:
        values["AWS_SECRET_ACCESS_KEY"] = values["R2_BACKUP_SECRET_ACCESS_KEY"]
    return values


def render(values: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Build every file's text in memory, refusing any value the env file formats cannot hold."""
    files: dict[str, str] = {}
    missing: set[str] = set()
    for service, keys in LAYOUT.items():
        lines = []
        for key in keys:
            source = (SENTRY_BY_SERVICE.get(service) if key == "SENTRY_DSN" else key) or key
            value = values.get(source)
            if value is None:
                missing.add(source)
                continue
            if "\n" in value:
                raise SystemExit(f"{key} contains a newline; refusing to write it")
            # Compose env files: a single-quoted value is taken literally (no `$` interpolation, quotes kept)
            # (reviews R9, R10). Compose's parser still treats a backslash before the closing quote as an escape,
            # so backslashes are refused too (review R11). backup.env is read by `docker run --env-file`, which is
            # literal already.
            if service == "backup":
                lines.append(f"{key}={value}")
            elif "'" in value:
                raise SystemExit(f"{key} contains a single quote; choose another value")
            elif "\\" in value:
                raise SystemExit(f"{key} contains a backslash; choose another value")
            else:
                lines.append(f"{key}='{value}'")
        files[f"{service}.env"] = "\n".join(lines) + "\n"
    return files, sorted(missing)


def write(out: Path, values: dict[str, str], *, required: set[str] = REQUIRED) -> list[str]:
    """Write all files or none: values and required keys are checked first, each file is replaced atomically."""
    files, missing = render(values)
    absent = sorted(required & set(missing))
    if absent:
        raise SystemExit("missing required keys, nothing written: " + ", ".join(absent))
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    for name, text in files.items():
        target = out / name
        temp = out / f".{name}.tmp"
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, target)
        print(f"wrote {name}")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("/srv/video-studio/env"))
    parser.add_argument("--infisical-path", default="/video-studio")
    args = parser.parse_args()
    try:
        missing = write(args.out, derive(load_secrets(args.infisical_path)))
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    if missing:
        print("missing optional keys: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
