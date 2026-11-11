"""Compare the LibreOffice-followed guide output with the engine output.

Usage: python compare.py ENGINE_JOB_DIR MANUAL_DIR LO_ENGINE_PDF
Prints a JSON report: DOCX structure, page setup, footer, table cells, PDF text and page counts.
"""

import json
import re
import sys
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

STYLE_ALIASES = {"Default Paragraph Style": "Normal", "Standard": "Normal", "Text Body": "Normal"}


def body(path):
    document = docx.Document(str(path))
    items = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            if paragraph.text.strip():
                style = paragraph.style.name if paragraph.style is not None else "?"
                items.append({"kind": "p", "style": STYLE_ALIASES.get(style, style), "text": paragraph.text})
        elif tag == "tbl":
            table = Table(child, document)
            items.append({"kind": "table", "cells": [[cell.text for cell in row.cells] for row in table.rows]})
    section = document.sections[0]
    footer = section.footer.paragraphs[0] if section.footer.paragraphs else None
    setup = {
        "page_mm": [round(section.page_width.mm), round(section.page_height.mm)],
        "top_bottom_in": [round(section.top_margin.inches, 2), round(section.bottom_margin.inches, 2)],
        "left_right_in": [round(section.left_margin.inches, 2), round(section.right_margin.inches, 2)],
        "footer_text": footer.text if footer is not None else None,
        "footer_has_page_field": footer is not None and "PAGE" in footer._p.xml,
        "footer_align": str(footer.alignment) if footer is not None else None,
    }
    return items, setup


def pdf_text(path):
    reader = PdfReader(str(path))
    return len(reader.pages), " ".join(page.extract_text() or "" for page in reader.pages)


def squash(text, drop=()):
    for value in drop:
        text = text.replace(value, "")
    return re.sub(r"\s+", "", text)


def main():
    engine_dir, manual_dir, lo_engine_pdf = map(Path, sys.argv[1:4])
    engine_items, engine_setup = body(engine_dir / "document.docx")
    manual_items, manual_setup = body(manual_dir / "document.docx")
    report = {
        "docx_body_equal": engine_items == manual_items,
        "docx_body_items": len(engine_items),
        "docx_body_diff": [
            {"index": index, "engine": e, "manual": m}
            for index, (e, m) in enumerate(zip(engine_items, manual_items, strict=False))
            if e != m
        ] + ([{"length": [len(engine_items), len(manual_items)]}] if len(engine_items) != len(manual_items) else []),
        "setup_engine": engine_setup,
        "setup_manual": manual_setup,
        "setup_equal": engine_setup == manual_setup,
    }
    engine_tables = [item["cells"] for item in engine_items if item["kind"] == "table"]
    manual_tables = [item["cells"] for item in manual_items if item["kind"] == "table"]
    report["table_cells_equal"] = engine_tables == manual_tables
    pages = {}
    texts = {}
    for name, path in (
        ("engine_pdf", engine_dir / "document.pdf"),
        ("manual_pdf", manual_dir / "document.pdf"),
        ("lo_engine_docx_pdf", lo_engine_pdf),
    ):
        pages[name], texts[name] = pdf_text(path)
    report["page_count"] = pages
    footer = engine_setup["footer_text"] or ""
    squashed = {name: squash(text, drop=(footer,)) for name, text in texts.items()}
    # Footer page numbers ("1") sit at the start or the end depending on the renderer.
    squashed = {name: re.sub(r"^1|1$", "", value) for name, value in squashed.items()}
    report["pdf_text_manual_equals_lo_engine"] = squashed["manual_pdf"] == squashed["lo_engine_docx_pdf"]
    report["pdf_text_manual_equals_engine"] = squashed["manual_pdf"] == squashed["engine_pdf"]
    report["pdf_text_chars"] = {name: len(value) for name, value in squashed.items()}
    critical = json.loads((engine_dir / "ir.json").read_text(encoding="utf-8"))
    expected = [f"{k}: {v} {critical['currency']}" for k, v in critical["derived_totals"].items()]
    expected.append("grand_total (ตัวอักษร): (สองหมื่นสี่พันเจ็ดสิบห้าบาทถ้วน)")
    report["critical_lines_in_manual_pdf"] = {line: squash(line) in squashed["manual_pdf"] for line in expected}
    if not report["pdf_text_manual_equals_engine"]:
        report["pdf_text_samples"] = {name: value[:400] for name, value in squashed.items()}
    print(json.dumps(report, ensure_ascii=False, indent=1))


main()
