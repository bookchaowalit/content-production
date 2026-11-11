"""Reverse guide drift test: docs/reverse/web-v1a/webflow.md must match the engine.

Built from the committed example page (``examples/web/landing-th.json`` + ``hero.png``)
with the real job runner, which is pure Python (no browser, fonts or network), so the
guide and the golden hashes are the same on any machine. Rewrite with
``REVERSE_GUIDES_UPDATE=1``.
"""

from __future__ import annotations

import json
from pathlib import Path

from production_trace.reverse import Golden, assert_reverse_guide, render_reverse_guide
from web_ir import compile_page
from web_production.flow import FLOW
from web_production.jobs import run_page_job
from web_production.manual import build_webflow_guide

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = "examples/web/landing-th.json"
TARGET = ROOT / "docs" / "reverse" / "web-v1a" / "webflow.md"
NOTES = {"text/html": "หน้า HTML ที่ engine เขียน", "text/css": "CSS ที่ engine เขียน", "image/png": "รูป hero ใน bundle (ไฟล์เดียวกับต้นทาง)"}


def build_text(output_dir: Path) -> str:
    page = json.loads((ROOT / EXAMPLE).read_text(encoding="utf-8"))["page"]
    result = run_page_job(page, output_dir, "reverse-guide-example", asset_root=ROOT / "examples" / "web")
    assert result.status == "accepted", result.error_code
    names = {artifact.media_type: artifact.filename for artifact in result.artifacts}
    guide = build_webflow_guide(compile_page(page), html_name=names["text/html"], css_name=names["text/css"], image_name=names.get("image/png"))
    return render_reverse_guide(
        tool_id="web-v1a",
        benchmark="Webflow",
        example=EXAMPLE,
        regenerate="cd services/web-production-api && REVERSE_GUIDES_UPDATE=1 pytest tests/test_reverse_guide.py",
        flow=FLOW,
        guide=guide,
        goldens=[Golden(artifact.filename, artifact.content_hash, NOTES[artifact.media_type]) for artifact in result.artifacts],
    )


def test_webflow_reverse_guide_matches_engine(tmp_path: Path) -> None:
    assert_reverse_guide(TARGET, build_text(tmp_path))


def test_flow_keeps_ai_parts_behind_logic() -> None:
    labels = [part.label for part in FLOW]
    assert labels.count("AI-ASSIST") == 1 and labels.count("HUMAN-GATE") == 2
    for index, part in enumerate(FLOW):
        if part.label == "AI-ASSIST":
            assert FLOW[index + 1].label == "LOGIC" and part.fallback
            assert part.engine == "web_production.provider:ProviderPageAdapter"


def test_ai_adapter_reaches_models_only_through_the_kit_gateway() -> None:
    source = (ROOT / "services" / "web-production-api" / "web_production" / "provider.py").read_text(encoding="utf-8")
    assert "from brief_adapter_kit.transport import GatewayTransport" in source
    for direct in ("import openai", "import anthropic", "import httpx", "import requests", "urllib.request"):
        assert direct not in source
