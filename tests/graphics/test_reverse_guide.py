"""Reverse guide drift test: docs/reverse/graphics-v1a/figma.md must match the engine.

Built from the committed example batch without a browser: the guide comes from
the compiled IR, the golden is the deterministic SVG. Rewrite with
``REVERSE_GUIDES_UPDATE=1``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from book_production_platform.graphics_production.flow import FLOW
from book_production_platform.graphics_production.manual import build_figma_guide
from book_production_platform.graphics_production.render import render_svg
from book_production_platform.graphics_production.templates import (
    compile_template,
    controls_for,
)
from book_production_platform.production_trace.reverse import (
    Golden,
    assert_reverse_guide,
    render_reverse_guide,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = "examples/graphics/batch.json"
TARGET = ROOT / "docs" / "reverse" / "graphics-v1a" / "figma.md"


def build_text() -> str:
    items = json.loads((ROOT / EXAMPLE).read_text(encoding="utf-8"))["items"]
    irs = [(item["request_id"], compile_template(item)) for item in items]
    controls = {item["request_id"]: controls_for(item) for item in items}
    goldens = [
        Golden(f"{name}.svg", hashlib.sha256(render_svg(ir).encode("utf-8")).hexdigest(), "SVG ที่ engine เขียน (ต้นฉบับ layer)")
        for name, ir in irs
    ]
    return render_reverse_guide(
        tool_id="graphics-v1a",
        benchmark="Figma",
        example=EXAMPLE,
        regenerate="REVERSE_GUIDES_UPDATE=1 uv run pytest tests/graphics/test_reverse_guide.py",
        flow=FLOW,
        guide=build_figma_guide(irs, batch_id="example", controls=controls),
        goldens=goldens,
    )


def test_figma_reverse_guide_matches_engine() -> None:
    assert_reverse_guide(TARGET, build_text())


def test_flow_keeps_ai_parts_behind_logic() -> None:
    labels = [part.label for part in FLOW]
    assert labels.count("AI-ASSIST") == 2 and "HUMAN-GATE" in labels
    for index, part in enumerate(FLOW):
        if part.label == "AI-ASSIST":
            assert FLOW[index + 1].label == "LOGIC" and part.fallback
