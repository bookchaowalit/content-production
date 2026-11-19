"""SPEC-001 R-CI-4 / AC-27: the per-module coverage gate does not average and does not round up."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_module_coverage.py"


def _report(tmp_path, files: dict[str, tuple[int, int]]) -> Path:
    data = {
        "files": {
            name: {"summary": {"num_statements": total, "covered_lines": covered, "percent_covered": 100 * covered / total}}
            for name, (covered, total) in files.items()
        }
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(data))
    return path


def _run(report: Path, *modules: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(report), *modules], capture_output=True, text=True)


def test_one_module_just_below_the_bar_fails_even_when_the_average_passes(tmp_path) -> None:
    report = _report(tmp_path, {"a.py": (8999, 10000), "b.py": (100, 100)})
    result = _run(report, "a.py", "b.py")
    assert result.returncode == 1
    assert "a.py 89.99" in result.stdout


def test_all_modules_at_or_above_the_bar_pass(tmp_path) -> None:
    report = _report(tmp_path, {"a.py": (90, 100), "b.py": (95, 100)})
    assert _run(report, "a.py", "b.py").returncode == 0


def test_a_listed_module_missing_from_the_report_fails(tmp_path) -> None:
    report = _report(tmp_path, {"a.py": (95, 100)})
    result = _run(report, "a.py", "missing.py")
    assert result.returncode == 1
    assert "missing.py not measured" in result.stdout
