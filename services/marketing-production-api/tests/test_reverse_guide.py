"""Reverse guide drift test: docs/reverse/marketing-v1a/meta-business-suite.md must match the engine.

Built from the committed example campaign (pure Python, no external program):
the guide comes from the packaged CampaignIR, the goldens are the package JSON
and the bulk CSV bytes. Rewrite with ``REVERSE_GUIDES_UPDATE=1``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from marketing_ir import CampaignIR
from marketing_production.channel_tools import BULK_COLUMNS
from marketing_production.flow import FLOW
from marketing_production.jobs import run_campaign_job
from marketing_production.manual import (
    SHEET_SECTION,
    build_marketing_guide,
    column_letter,
)
from production_trace import ManualGuide
from production_trace.reverse import Golden, assert_reverse_guide, render_reverse_guide

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = "examples/marketing/campaign.json"
TARGET = ROOT / "docs" / "reverse" / "marketing-v1a" / "meta-business-suite.md"


def build_example(out: Path) -> tuple[ManualGuide, dict[str, Any], bytes, list[Golden]]:
    campaign = json.loads((ROOT / EXAMPLE).read_text(encoding="utf-8"))
    campaign["asset_root"] = str((ROOT / EXAMPLE).parent)
    result = run_campaign_job(campaign, out, "reverse-guide")
    assert result.status == "accepted" and result.package is not None, result.error_code
    manifest = json.loads((out / str(result.manifest_filename)).read_text(encoding="utf-8"))
    package_bytes = (out / result.package.filename).read_bytes()
    csv_name = manifest["exports"][0]["filename"]
    csv_bytes = (out / csv_name).read_bytes()
    package = json.loads(package_bytes)
    ir = CampaignIR.model_validate(package["campaign"])
    guide = build_marketing_guide(ir, links=package.get("links", []), experiments=package.get("experiments", []), checks=package.get("checks", []))
    goldens = [
        Golden(result.package.filename, hashlib.sha256(package_bytes).hexdigest(), "แพ็กเกจ JSON (แบบร่างทุกช่องทาง)"),
        Golden(csv_name, hashlib.sha256(csv_bytes).hexdigest(), "bulk CSV: เทียบทีละเซลล์กับไฟล์ที่ Calc บันทึก"),
    ]
    return guide, package, csv_bytes, goldens


def build_text(out: Path) -> str:
    guide, _, _, goldens = build_example(out)
    return render_reverse_guide(
        tool_id="marketing-v1a",
        benchmark=guide.program,
        example=EXAMPLE,
        regenerate="cd services/marketing-production-api && REVERSE_GUIDES_UPDATE=1 pytest tests/test_reverse_guide.py",
        flow=FLOW,
        guide=guide,
        goldens=goldens,
    )


def test_meta_business_suite_reverse_guide_matches_engine(tmp_path: Path) -> None:
    assert_reverse_guide(TARGET, build_text(tmp_path))


def test_sheet_steps_rebuild_the_bulk_csv_cell_by_cell(tmp_path: Path) -> None:
    guide, _, csv_bytes, _ = build_example(tmp_path)
    cells = {step.target: step.value for step in guide.steps if step.section == SHEET_SECTION and step.target}
    engine = list(csv.reader(io.StringIO(csv_bytes.decode("utf-8"))))
    rebuilt = [[cells.get(f"{column_letter(col)}{row}", "") for col in range(len(BULK_COLUMNS))] for row in range(1, len(engine) + 1)]
    assert rebuilt == engine


def test_flow_keeps_ai_parts_behind_logic() -> None:
    labels = [part.label for part in FLOW]
    assert labels.count("AI-ASSIST") == 1 and labels[-1] == "HUMAN-GATE"
    for index, part in enumerate(FLOW):
        if part.label == "AI-ASSIST":
            assert FLOW[index + 1].label == "LOGIC" and part.fallback
