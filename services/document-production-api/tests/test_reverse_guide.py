"""Reverse guide drift test: docs/reverse/documents-v1a/word.md must match the engine.

Built from the committed example request with no font, PDF renderer or
office program: the guide comes from the validated IR, the goldens are
``ir.json`` (as the job writes it) and the DOCX body and styles XML (python-docx output
is the same on every machine; the PDF is not hashed because it embeds the
local Thai font and a timestamp). Rewrite with ``REVERSE_GUIDES_UPDATE=1``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from document_production.flow import FLOW
from document_production.jobs import _atomic_json
from document_production.manual import build_word_guide
from document_production.recipe import build_document_ir
from document_production.renderers import _render_docx
from production_trace.reverse import Golden, assert_reverse_guide, render_reverse_guide

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = "services/document-production-api/examples/invoice-wht-th.json"
TARGET = ROOT / "docs" / "reverse" / "documents-v1a" / "word.md"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_text(workdir: Path) -> str:
    ir = build_document_ir(json.loads((ROOT / EXAMPLE).read_text(encoding="utf-8")))
    _atomic_json(workdir / "ir.json", ir.model_dump(mode="json"))
    _render_docx(ir, workdir)
    with ZipFile(workdir / "document.docx") as archive:
        body = archive.read("word/document.xml")
        styles = archive.read("word/styles.xml")
    goldens = [
        Golden("ir.json", _sha256((workdir / "ir.json").read_bytes()), "IR ที่ผ่านการตรวจ: ทุกค่าและยอดที่ต้องพิมพ์"),
        Golden("document.docx/word/document.xml", _sha256(body), "เนื้อหา DOCX ที่ engine เขียน (หน้า A4 ขอบ ตาราง ยอด)"),
        Golden("document.docx/word/styles.xml", _sha256(styles), "สไตล์ของ DOCX (ฟอนต์ Normal = Sarabun)"),
    ]
    return render_reverse_guide(
        tool_id="documents-v1a",
        benchmark="Microsoft Word",
        example=EXAMPLE,
        regenerate="cd services/document-production-api && REVERSE_GUIDES_UPDATE=1 python -m pytest tests/test_reverse_guide.py",
        flow=FLOW,
        guide=build_word_guide(ir),
        goldens=goldens,
    )


def test_word_reverse_guide_matches_engine(tmp_path, monkeypatch):
    # Nothing machine-dependent may reach the guide: hide any configured font.
    monkeypatch.delenv("DOCUMENT_PRODUCTION_FONT", raising=False)
    assert_reverse_guide(TARGET, build_text(tmp_path))


def test_flow_keeps_the_brief_adapter_behind_logic():
    labels = [part.label for part in FLOW]
    assert labels.count("AI-ASSIST") == 1 and "HUMAN-GATE" in labels
    for index, part in enumerate(FLOW):
        if part.label == "AI-ASSIST":
            assert FLOW[index + 1].label == "LOGIC" and part.fallback
            assert "ProviderBriefAdapter" in part.engine
