"""Compare an engine render with a hand-made export (reverse-guide verification).

Usage:
    uv run python scripts/reverse_image_diff.py ENGINE.png MANUAL.png \
        [--region name=x0,y0,x1,y1 ...] [--threshold 16] [--diff OUT.png] [--json]

Prints, for the whole image and each region, the share of pixels whose
largest per-channel difference (R, G, B or alpha) is above ``threshold``. Exit code 2 when sizes differ.
Used for docs/reverse/<tool>/VERIFY.md evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageChops


def _share(diff: Image.Image, threshold: int) -> float:
    histogram = diff.histogram()
    total = sum(histogram)
    return round(100 * sum(histogram[threshold + 1 :]) / total, 2) if total else 0.0


def compare(
    engine: Path, manual: Path, regions: dict[str, tuple[int, int, int, int]], threshold: int = 16
) -> dict[str, object]:
    with Image.open(engine) as a_raw, Image.open(manual) as b_raw:
        a, b = a_raw.convert("RGBA"), b_raw.convert("RGBA")
    if a.size != b.size:
        return {"size_engine": list(a.size), "size_manual": list(b.size), "error": "size_mismatch"}
    # Largest per-channel difference, alpha included: a transparent pixel never matches an opaque one.
    bands = ImageChops.difference(a, b).split()
    diff = bands[0]
    for band in bands[1:]:
        diff = ImageChops.lighter(diff, band)
    return {
        "size": list(a.size),
        "threshold": threshold,
        "whole_percent": _share(diff, threshold),
        "regions": {name: _share(diff.crop(box), threshold) for name, box in regions.items()},
        "_diff": diff,
    }


def _region(text: str) -> tuple[str, tuple[int, int, int, int]]:
    name, _, coords = text.partition("=")
    parts = tuple(int(value) for value in coords.split(","))
    if not name or len(parts) != 4:
        raise argparse.ArgumentTypeError("region must be name=x0,y0,x1,y1")
    return name, (parts[0], parts[1], parts[2], parts[3])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare an engine render with a hand-made export.")
    parser.add_argument("engine", type=Path)
    parser.add_argument("manual", type=Path)
    parser.add_argument("--region", type=_region, action="append", default=[])
    parser.add_argument("--threshold", type=int, default=16)
    parser.add_argument("--diff", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = compare(args.engine, args.manual, dict(args.region), args.threshold)
    diff = result.pop("_diff", None)
    if args.diff is not None and isinstance(diff, Image.Image):
        diff.point(lambda value: 255 if value > args.threshold else 0).save(args.diff)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
    return 2 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
