"""Local DOCX and PDF renderers for normalized document IR."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from document_ir import ArtifactRef, DocumentImage, DocumentIR
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .thai_text import baht_text, has_thai, wrap_lines

SUPPORTED_TEMPLATES = frozenset({"invoice@1", "quotation@1", "proposal@1", "transcript@1", "report@1", "spec-sheet@1"})
PDF_FONT_NAME = "DocumentProductionUnicode"
DOCX_SIDE_MARGIN_INCHES = 1.25
# Font the DOCX "Normal" style names for Latin and complex (Thai) script; the
# Word guide tells the person to set the same one. Headings inherit the Thai face.
DOCX_FONT = "Sarabun"


class RenderError(RuntimeError):
    """Raised when a deterministic artifact cannot be rendered."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _header_lines(ir: DocumentIR) -> tuple[str, str]:
    """Customer and issue-date lines in the document language (Thai for th-* locales)."""

    if ir.locale.lower().startswith("th"):
        return f"ลูกค้า: {ir.customer_name}", f"วันที่ออกเอกสาร: {ir.issue_date.isoformat()}"
    return f"Customer: {ir.customer_name}", f"Issue date: {ir.issue_date.isoformat()}"


def _pdf_column_widths(ir: DocumentIR, columns: int) -> list[float]:
    if ir.template_ref == "spec-sheet@1" and columns == 3:
        return [80 * mm, 70 * mm, 30 * mm]
    return [65 * mm, 20 * mm, 28 * mm, 20 * mm, 28 * mm]


def _verified_image(image: DocumentImage) -> str:
    """Re-hash an embedded image right before rendering so a swapped file is never used."""

    path = Path(image.path)
    if not path.is_file() or _sha256(path) != image.content_hash:
        raise RenderError(f"embedded image changed or is missing: {path.name}")
    return str(path)


def _atomic_path(output_dir: Path, filename: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / filename
    with tempfile.NamedTemporaryFile(prefix=f".{filename}.", suffix=".tmp", dir=output_dir, delete=False) as handle:
        return Path(handle.name), final_path


def _artifact(path: Path, filename: str, media_type: str, page_count: int | None = None) -> ArtifactRef:
    return ArtifactRef(
        filename=filename,
        media_type=media_type,
        content_hash=_sha256(path),
        byte_size=path.stat().st_size,
        page_count=page_count,
    )


def _footer_label(ir: DocumentIR) -> str:
    """Footer prefix shown before the page number on every page (DOCX and PDF)."""

    return f"{ir.document_id} · หน้า " if ir.locale.lower().startswith("th") else f"{ir.document_id} · Page "


def amount_in_words_line(ir: DocumentIR) -> str | None:
    """``grand_total (ตัวอักษร): (…บาทถ้วน)`` for Thai-locale THB documents with totals, else ``None``."""

    grand_total = ir.derived_totals.get("grand_total")
    if ir.currency != "THB" or not ir.locale.lower().startswith("th") or grand_total is None:
        return None
    try:
        amount = Decimal(grand_total)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return f"grand_total (ตัวอักษร): ({baht_text(amount)})"


def _add_docx_page_footer(document: Any, ir: DocumentIR) -> None:
    """Add ``<document_id> · Page <PAGE field>`` to the DOCX footer (Insert ▸ Page Number in Word)."""

    paragraph = document.sections[0].footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    paragraph.add_run(_footer_label(ir))
    run = paragraph.add_run()
    for tag, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag is None:
            instruction = OxmlElement("w:instrText")
            instruction.set(qn("xml:space"), "preserve")
            instruction.text = text
            run._r.append(instruction)
        else:
            field = OxmlElement("w:fldChar")
            field.set(qn("w:fldCharType"), tag)
            run._r.append(field)


def _set_docx_font(document: Any) -> None:
    normal = document.styles["Normal"]
    normal.font.name = DOCX_FONT
    fonts = normal.element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:cs"), DOCX_FONT)


def _render_docx(ir: DocumentIR, output_dir: Path) -> ArtifactRef:
    temporary, final = _atomic_path(output_dir, "document.docx")
    try:
        document = Document()
        section = document.sections[0]
        # A4 like the PDF (python-docx's default template is US Letter).
        section.page_width = Mm(210)
        section.page_height = Mm(297)
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = section.right_margin = Inches(DOCX_SIDE_MARGIN_INCHES)
        _set_docx_font(document)
        _add_docx_page_footer(document, ir)
        document.add_heading(ir.title, level=0)
        for line in _header_lines(ir):
            document.add_paragraph(line)
        for item_section in ir.sections:
            document.add_heading(item_section.heading, level=1)
            for paragraph in item_section.paragraphs:
                document.add_paragraph(paragraph)
            for item_table in item_section.tables:
                table = document.add_table(rows=1, cols=len(item_table.headers))
                table.style = "Table Grid"
                for cell, value in zip(table.rows[0].cells, item_table.headers):
                    cell.text = value
                for row in item_table.rows:
                    cells = table.add_row().cells
                    for cell, value in zip(cells, row.cells):
                        cell.text = value
            for image in item_section.images:
                document.add_picture(_verified_image(image), width=Inches(6.3))
                if image.caption:
                    document.add_paragraph(image.caption)
        # The Totals block is invoice-specific; templates without monetary
        # totals (e.g. transcript@1) publish an empty derived_totals mapping.
        if ir.derived_totals:
            document.add_heading("Totals", level=1)
            for name, value in ir.derived_totals.items():
                document.add_paragraph(f"{name}: {value} {ir.currency}")
            words = amount_in_words_line(ir)
            if words:
                document.add_paragraph(words)
        document.save(str(temporary))
        os.replace(temporary, final)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise RenderError(f"DOCX render failed: {exc}") from exc
    return _artifact(final, "document.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# Built-in Thai fonts, tried in order when DOCUMENT_PRODUCTION_FONT is unset.
# Each must also cover Latin letters and digits: PDF text QA reads dates and
# totals back, so a Thai-only face (e.g. NotoSansThai) is never a fallback.
PDF_FONT_CANDIDATES = (
    "C:/Windows/Fonts/LeelawUI.ttf",
    "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
    "/usr/share/fonts/truetype/sarabun/Sarabun-Regular.ttf",
    "/usr/share/fonts/truetype/google-fonts/Sarabun-Regular.ttf",
    "/usr/share/fonts/truetype/tlwg/Loma.ttf",
)
# Printable ASCII (Latin letters, digits, punctuation) plus every assigned Thai
# codepoint: consonants, vowels, tone marks, Baht sign and Thai digits.
PDF_REQUIRED_CODEPOINTS = frozenset([*range(0x21, 0x7F), *range(0x0E01, 0x0E3B), *range(0x0E3F, 0x0E5C)])


def _missing_codepoints(char_to_glyph: Mapping[int, int]) -> list[int]:
    # Glyph 0 is .notdef: the cmap lists the codepoint but draws a blank box.
    return sorted(codepoint for codepoint in PDF_REQUIRED_CODEPOINTS if not char_to_glyph.get(codepoint))


def _load_font(path: str) -> TTFont:
    font = TTFont(PDF_FONT_NAME, path)
    missing = _missing_codepoints(font.face.charToGlyph)
    if missing:
        sample = "".join(chr(codepoint) for codepoint in missing[:10])
        raise RenderError(f"PDF font {path} lacks {len(missing)} required glyphs (e.g. {sample!r}); it must cover Latin, digits and Thai")
    return font


def _font_name() -> str:
    if PDF_FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return PDF_FONT_NAME
    configured = os.environ.get("DOCUMENT_PRODUCTION_FONT")
    if configured:
        # An explicit font that is missing or incomplete is a deployment error,
        # not something to paper over with a built-in fallback.
        if not Path(configured).exists():
            raise RenderError(f"DOCUMENT_PRODUCTION_FONT {configured} does not exist")
        pdfmetrics.registerFont(_load_font(configured))
        return PDF_FONT_NAME
    for candidate in PDF_FONT_CANDIDATES:
        if not Path(candidate).exists():
            continue
        try:
            font = _load_font(candidate)
        except (RenderError, TTFError, OSError):
            continue  # unreadable or incomplete; try the next built-in font
        pdfmetrics.registerFont(font)
        return PDF_FONT_NAME
    return "Helvetica"


def _pdf_value(value: str, font_name: str) -> str:
    if font_name != "Helvetica":
        return escape(value)
    if any(ord(character) > 255 for character in value):
        raise RenderError("PDF font cannot encode non-Latin text; configure DOCUMENT_PRODUCTION_FONT")
    return escape(value)


# Text width inside the page frame (A4 minus 15 mm margins and the 6 pt frame
# padding each side) and inside a table cell (6 pt cell padding each side).
PDF_FRAME_WIDTH = A4[0] - 30 * mm - 12
PDF_CELL_PADDING = 12


def _pdf_text(value: str, font_name: str, font_size: float, width: float | None) -> str:
    """Escape ``value``; Thai text is broken into lines here so no Thai cluster is split (spec D3)."""

    text = _pdf_value(value, font_name)
    if width is None or font_name == "Helvetica" or not has_thai(value):
        return text
    # A hair under the width: ReportLab must never need to re-wrap a line we made.
    lines = wrap_lines(value, width - 0.5, lambda line: stringWidth(line, font_name, font_size))
    return "<br/>".join(escape(line) for line in lines)


def _render_pdf(ir: DocumentIR, output_dir: Path) -> ArtifactRef:
    temporary, final = _atomic_path(output_dir, "document.pdf")
    try:
        font_name = _font_name()
        styles = getSampleStyleSheet()
        body = ParagraphStyle("DocumentBody", parent=styles["BodyText"], fontName=font_name, fontSize=10, leading=14, alignment=TA_LEFT)
        heading = ParagraphStyle("DocumentHeading", parent=styles["Heading1"], fontName=font_name, fontSize=14, leading=18)
        frame = PDF_FRAME_WIDTH

        def text(value: str, style: ParagraphStyle, width: float | None = frame) -> Paragraph:
            return Paragraph(_pdf_text(value, font_name, style.fontSize, width), style)

        story: list[Any] = [text(ir.title, heading), Spacer(1, 4 * mm)]
        for line in _header_lines(ir):
            story.append(text(line, body))
        for item_section in ir.sections:
            story.append(Spacer(1, 4 * mm))
            story.append(text(item_section.heading, heading))
            for paragraph in item_section.paragraphs:
                story.append(text(paragraph, body))
            for item_table in item_section.tables:
                widths = _pdf_column_widths(ir, len(item_table.headers))

                def cell(index: int, value: str, widths: list[float] = widths) -> Paragraph:
                    width = widths[index] - PDF_CELL_PADDING if index < len(widths) else None
                    return text(value, body, width)

                data = [[cell(index, value) for index, value in enumerate(item_table.headers)]]
                data.extend([[cell(index, value) for index, value in enumerate(row.cells)] for row in item_table.rows])
                table = Table(data, repeatRows=1, colWidths=widths)
                table.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
                story.append(Spacer(1, 2 * mm))
                story.append(table)
            for image in item_section.images:
                story.append(Spacer(1, 3 * mm))
                story.append(Image(_verified_image(image), width=170 * mm, height=120 * mm, kind="proportional"))
                if image.caption:
                    story.append(text(image.caption, body))
        if ir.derived_totals:
            story.append(Spacer(1, 4 * mm))
            story.append(text("Totals", heading))
            for name, value in ir.derived_totals.items():
                story.append(text(f"{name}: {value} {ir.currency}", body))
            words = amount_in_words_line(ir)
            if words:
                story.append(text(words, body))

        document = SimpleDocTemplate(str(temporary), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
        label = _pdf_value(_footer_label(ir), font_name)

        def footer(canvas: Any, template: Any) -> None:
            canvas.saveState()
            canvas.setFont(font_name, 8)
            canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"{label}{template.page}")
            canvas.restoreState()

        document.build(story, onFirstPage=footer, onLaterPages=footer)
        os.replace(temporary, final)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise RenderError(f"PDF render failed: {exc}") from exc
    page_count = len(PdfReader(str(final)).pages)
    return _artifact(final, "document.pdf", "application/pdf", page_count=page_count)


def render_document(ir: DocumentIR, output_dir: str | Path) -> list[ArtifactRef]:
    """Render the supported IR into local DOCX and PDF artifacts."""

    if ir.template_ref not in SUPPORTED_TEMPLATES:
        raise RenderError(f"template {ir.template_ref!r} is not supported")
    output_path = Path(output_dir)
    return [_render_docx(ir, output_path), _render_pdf(ir, output_path)]
