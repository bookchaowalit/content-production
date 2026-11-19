#!/usr/bin/env python3
"""Per-module line-coverage gate (SPEC-001 R-CI-4): every listed module must reach the bar on its own.

    python scripts/check_module_coverage.py coverage.json video_postproduction/retention.py ... [--min 90]

Uses covered/statements with two decimals and no rounding up; a listed module missing from the report fails.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("modules", nargs="+")
    parser.add_argument("--min", type=float, default=90.0)
    args = parser.parse_args(argv)
    files = json.loads(args.report.read_text())["files"]
    failed = False
    for module in args.modules:
        summary = files.get(module, {}).get("summary")
        if summary is None:
            print(f"{module} not measured")
            failed = True
            continue
        total = summary["num_statements"]
        percent = 100.0 if total == 0 else math.floor(10000 * summary["covered_lines"] / total) / 100
        status = "ok" if percent >= args.min else "below"
        print(f"{module} {percent:.2f} {status}")
        failed = failed or percent < args.min
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
