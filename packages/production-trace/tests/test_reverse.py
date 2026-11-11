from __future__ import annotations

from pathlib import Path

import pytest
from production_trace import ManualGuide
from production_trace.reverse import (
    FlowError,
    FlowPart,
    Golden,
    assert_reverse_guide,
    render_reverse_guide,
    validate_flow,
)

LOGIC = FlowPart("layout", "LOGIC", engine="tool.layout:compile", trace_stage="compile")
AI = FlowPart(
    "brief to IR",
    "AI-ASSIST",
    engine="tool.brief:propose",
    trace_stage="ai",
    manual_note="เลือก template และกรอกข้อความเอง",
    fallback="ใช้ template เริ่มต้นจาก IR ที่กรอกมือ",
)
GATE = FlowPart("approve text", "HUMAN-GATE", engine="review.html", trace_stage="review", manual_note="ลูกค้าอนุมัติข้อความ")


def _guide() -> ManualGuide:
    guide = ManualGuide(program="Figma", title="demo", setup=["เปิด Figma"])
    guide.layer("frame", "Frame", position="W=1080")
    guide.step("วาด Frame", tool="Frame (F)", value="1080×1080")
    guide.checks = ["ขนาด 1080×1080"]
    return guide


def _render() -> str:
    return render_reverse_guide(
        tool_id="graphics-v1a",
        benchmark="Figma",
        example="examples/graphics/batch.json",
        regenerate="REVERSE_GUIDES_UPDATE=1 uv run pytest tests/graphics/test_reverse_guide.py",
        flow=[AI, LOGIC, GATE],
        guide=_guide(),
        goldens=[Golden("demo.svg", "ab" * 32, "SVG ที่ engine เขียน")],
    )


@pytest.mark.parametrize(
    ("flow", "code"),
    [
        ([], "flow_empty"),
        ([GATE], "flow_no_logic"),
        ([FlowPart("x", "MAGIC", engine="e", trace_stage="s"), LOGIC], "flow_label_unknown"),
        ([FlowPart("x", "AI-ASSIST", engine="e", trace_stage="s"), LOGIC], "flow_ai_no_fallback"),
        ([LOGIC, AI], "flow_ai_unvalidated"),
        ([LOGIC, AI, GATE], "flow_ai_unvalidated"),
    ],
)
def test_validate_flow_rejects_unsafe_flows(flow: list[FlowPart], code: str) -> None:
    with pytest.raises(FlowError) as raised:
        validate_flow(flow)
    assert raised.value.code == code


def test_validate_flow_accepts_ai_followed_by_logic() -> None:
    validate_flow([AI, LOGIC, GATE])


def test_render_is_deterministic_and_complete() -> None:
    text = _render()
    assert text == _render()
    assert "# Reverse guide: graphics-v1a → Figma" in text
    assert "| 1 | brief to IR | `AI-ASSIST` |" in text
    assert "ใช้ template เริ่มต้นจาก IR ที่กรอกมือ" in text
    assert "## 3. ขั้นตอนทีละคลิก" in text
    assert "1. วาด Frame" in text
    assert "`demo.svg`" in text and "ab" * 32 in text
    assert "/home/" not in text and "/tmp/" not in text


def test_render_rejects_absolute_paths_and_bad_flows() -> None:
    with pytest.raises(FlowError):
        render_reverse_guide(tool_id="t", benchmark="b", example="/tmp/x.json", regenerate="r", flow=[LOGIC], guide=_guide(), goldens=[])
    with pytest.raises(FlowError):
        render_reverse_guide(tool_id="t", benchmark="b", example="x.json", regenerate="r", flow=[GATE], guide=_guide(), goldens=[])


def test_assert_reverse_guide_detects_missing_and_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "docs" / "reverse" / "graphics-v1a" / "figma.md"
    monkeypatch.delenv("REVERSE_GUIDES_UPDATE", raising=False)
    with pytest.raises(AssertionError, match="REVERSE_GUIDES_UPDATE=1"):
        assert_reverse_guide(target, _render())
    monkeypatch.setenv("REVERSE_GUIDES_UPDATE", "1")
    assert_reverse_guide(target, _render())
    assert target.read_text(encoding="utf-8") == _render()
    monkeypatch.delenv("REVERSE_GUIDES_UPDATE")
    assert_reverse_guide(target, _render())
    target.write_text(_render() + "edited\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="drifted"):
        assert_reverse_guide(target, _render())
