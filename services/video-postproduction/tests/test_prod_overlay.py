"""SPEC-001 R-COMPOSE-1/2/3: the production overlay, checked on the merged `docker compose config` (AC-26)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CHECK = REPO / "scripts" / "studio" / "check_config.py"

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run(["docker", "compose", "version"], check=False, capture_output=True).returncode != 0,
    reason="needs docker compose",
)


def test_check_config_passes_on_the_committed_overlay() -> None:
    result = subprocess.run([sys.executable, str(CHECK)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


def test_check_config_catches_a_secret_in_the_wrong_service(tmp_path) -> None:
    broken = tmp_path / "docker-compose.prod.yml"
    text = (REPO / "services/video-postproduction/docker-compose.prod.yml").read_text()
    # Give the worker the web env file too: the API token would then reach the worker.
    text = text.replace(
        'env_file: ["${STUDIO_ENV_DIR:?set STUDIO_ENV_DIR}/worker.env"]',
        'env_file: ["${STUDIO_ENV_DIR:?set STUDIO_ENV_DIR}/worker.env", "${STUDIO_ENV_DIR:?set STUDIO_ENV_DIR}/web.env"]',
    )
    broken.write_text(text)
    result = subprocess.run(
        [sys.executable, str(CHECK), "--overlay", str(broken)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "VIDEO_EDITOR_API_TOKEN" in result.stdout and "worker" in result.stdout
