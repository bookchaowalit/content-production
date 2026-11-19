#!/usr/bin/env python3
"""Check the merged production Compose config (SPEC-001 R-COMPOSE-1/2/3, AC-26).

Renders `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` with throwaway env files that hold
sentinel values, then checks that:

- every secret reaches only the services allowed to have it;
- no base default shadows an env-file value (the sentinel survives the merge);
- no service publishes a port; web shares the tailscale network namespace;
- every service restarts and has a memory limit; images use STUDIO_TAG.

Never reads real secrets. Exit 0 = ok, 1 = a rule failed. Prints one line per problem.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE_DIR = REPO / "services" / "video-postproduction"

# secret name -> services allowed to see it; the env file it is written to
SECRETS: dict[str, tuple[set[str], str]] = {
    "VIDEO_EDITOR_API_TOKEN": ({"api", "web"}, "api,web"),
    "DATABASE_URL": ({"api", "worker"}, "api,worker"),
    "POSTGRES_PASSWORD": ({"postgres"}, "postgres"),
    "TS_AUTHKEY": ({"tailscale"}, "tailscale"),
    "SENTRY_DSN": ({"api", "worker", "web"}, "api,worker,web"),
    "STUDIO_OWNER_LOGIN": ({"web"}, "web"),
    "STUDIO_PUBLIC_HOST": ({"web"}, "web"),
}
ENV_FILES = ("postgres", "api", "worker", "web", "tailscale")


def sentinel(name: str) -> str:
    return f"SENTINEL-{name}-do-not-use"


def render(overlay: Path, env_dir: Path) -> dict:
    for service in ENV_FILES:
        lines = [f"{name}={sentinel(name)}" for name, (_, files) in SECRETS.items() if service in files.split(",")]
        (env_dir / f"{service}.env").write_text("\n".join(lines) + "\n")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_DIR / "docker-compose.yml"),
            "-f",
            str(overlay),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        cwd=COMPOSE_DIR,
        env={**os.environ, "STUDIO_TAG": "studio-check", "STUDIO_ENV_DIR": str(env_dir)},
    )
    if result.returncode != 0:
        print(f"compose config failed: {result.stderr.strip()}")
        sys.exit(1)
    return json.loads(result.stdout)


def check(config: dict) -> list[str]:
    problems: list[str] = []
    services: dict[str, dict] = config["services"]
    for service_name, service in services.items():
        environment = service.get("environment") or {}
        for secret, (allowed, _) in SECRETS.items():
            value = environment.get(secret)
            if service_name in allowed:
                if value != sentinel(secret):
                    problems.append(f"{secret} in {service_name} is {'missing' if value is None else 'shadowed'}")
            elif value is not None:
                problems.append(f"{secret} reaches {service_name}, which must not have it")
            for key, other in environment.items():
                if key != secret and isinstance(other, str) and sentinel(secret) in other:
                    problems.append(f"{secret} leaks into {service_name}.{key}")
        if service.get("ports"):
            problems.append(f"{service_name} publishes ports {service['ports']}")
        if service.get("restart") != "unless-stopped":
            problems.append(f"{service_name} has no restart: unless-stopped")
        if not service.get("mem_limit"):
            problems.append(f"{service_name} has no mem_limit")
    if services.get("web", {}).get("network_mode") != "service:tailscale":
        problems.append("web must use network_mode: service:tailscale")
    for name in ("api", "worker", "web"):
        if not str(services.get(name, {}).get("image", "")).endswith(":studio-check"):
            problems.append(f"{name} image does not use STUDIO_TAG")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overlay", type=Path, default=COMPOSE_DIR / "docker-compose.prod.yml")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="studio-env-") as tmp:
        problems = check(render(args.overlay, Path(tmp)))
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
