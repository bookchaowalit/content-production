# documents-v1a — reverse guide verification

Rule: [reverse guides spec R4](../../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md).

| Date | Guide | Program | Followed by | Method | Result |
|---|---|---|---|---|---|
| 2026-11-12 | [word.md](word.md) | LibreOffice Writer 7.4.7.2 (Debian bookworm, headless, Docker), the guide's listed alternative; Microsoft Word is not installed here | Claude agent via Python UNO ([follow_word_guide.py](evidence/follow_word_guide.py)): each of the 27 numbered steps as one UNO operation with the guide's exact value, dispatched by the step's Word menu path (an unknown menu path stops the run) | Engine job from `services/document-production-api/examples/invoice-wht-th.json` vs Writer's own DOCX + PDF export: DOCX body (paragraph style + text, table cells) and page setup with python-docx ([compare.py](evidence/compare.py)); PDF text (pdftotext / pypdf); page count; 100 dpi rasters with `scripts/reverse_image_diff.py`, threshold 16/255 | **Pass with tolerance** (below) |

## 2026-11-12 run

Evidence: [followed guide](evidence/libreoffice-followed-guide.png) ·
[engine DOCX opened in Writer](evidence/libreoffice-engine-docx.png) ·
[diff mask](evidence/diff-followed-vs-engine-docx.png) ·
[engine PDF (ReportLab)](evidence/engine-pdf.png) ·
[structure](evidence/compare.json) · [text](evidence/text-diff.json) ·
[image](evidence/image-diff.json) · [steps executed](evidence/replay-log.json).
The engine's `ir.json`, `word/document.xml` and `word/styles.xml` match the
goldens in `word.md`.

| Check | Result |
|---|---|
| DOCX body: 13 items (paragraph style + text, then the table) | **identical** (Title, Normal ×2, Heading 1, Normal, table, Heading 1, Normal ×6) |
| Table cells (3 × 5) | **identical** |
| Totals, withholding tax, net payable, amount in Thai words in the followed PDF | all 6 lines present, same values as the engine |
| Page size, top margin, left/right margins | identical: A4 210 × 297 mm, 0.7", 1.25" |
| Footer: right aligned, `example-invoice-wht-002 · หน้า ` + PAGE field | identical (Writer also stores the field result `1`) |
| Page count (engine PDF, followed PDF, engine DOCX in Writer) | 1 / 1 / 1 |
| PDF text, whitespace removed: followed guide vs engine DOCX rendered by Writer | **identical** (491 characters) |
| PDF text: followed guide vs engine PDF (ReportLab) | same 491 characters; one word (`รายเดือน`) in a different order because the Word table wraps the description cell and the PDF table does not |
| Pixels differing, followed guide vs engine DOCX, both rendered by Writer | 5.27 % of the page (title 9.81 %, body 12.87 %, footer 15.58 %) |

The pixel difference comes from what each style name looks like, not from a
wrong step: the engine DOCX carries python-docx's template definitions (Title
26 pt blue with a bottom rule, Heading 1 blue 14 pt, Normal 11 pt with 10 pt
space after), while a new Writer document uses Writer's own built-ins for the
same names (Title centred bold, Heading 1 black bold, Default Paragraph Style
12 pt without space after, bold centred "Table Heading" in the first table
row). A new Word 365 document would use Word 365's built-ins (Aptos), which
also differ from python-docx's template. The guide pins the style *names*,
text, values, page setup and the Normal font, not the look of each built-in
style; that gap is a P2 row in the parity matrix. Text, values, structure, page
geometry and page count match.

### Findings fixed (engine + guide builder)

1. **Paper size.** The guide said A4 but the engine DOCX was US Letter
   (216 × 279 mm, python-docx's template default); the PDF was already A4. The
   DOCX is now A4, and the guide also gives the left/right margins (it gave only
   top/bottom, so Writer kept 0.79" and Word would keep 1").
2. **Font.** The guide said "use a Thai font such as TH Sarabun New or Sarabun"
   but the DOCX named no Thai font, so the engine DOCX looked different on every
   machine. The DOCX `Normal` style now names `Sarabun` for Latin and complex
   script, and the guide has one step that sets it
   (`Home ▸ Styles ▸ Normal ▸ Modify… ▸ Format ▸ Font…`).
3. **Example title.** The first example called itself `ใบแจ้งหนี้ / ใบกำกับภาษี`;
   the engine does not print the seller's tax ID or address that a Thai full
   tax invoice needs, so the example is now just `ใบแจ้งหนี้` (the gap is a P2
   row in the parity matrix).

Tests for 1–2: `test_docx_page_is_a4_like_the_pdf_and_the_word_guide`,
`test_docx_declares_the_thai_font_the_word_guide_names`.

### LibreOffice-vs-Word differences seen (not guide errors)

- Built-in style definitions (above) and Writer's automatic "Table Heading" /
  "Table Contents" paragraph styles in table cells.
- Writer has no "Table Grid" table style; step 15 is executed as Word's Table
  Grid look (single 0.5 pt black border on every cell).
- Writer exports a footer by adding its height to the bottom page margin
  (0.92" in the followed DOCX vs 0.7"); the footer's position on the page is
  Writer's.
- Writer saves the PAGE field with its current result (`1`); python-docx
  writes the field without a cached result.

### Not covered by this run

- Microsoft Word itself and Google Docs.
- The other templates (quotation, proposal, report with images, transcript,
  spec sheet) and their guides.
- Thai text that wraps inside the engine PDF table cells (covered by
  `tests/test_thai_text.py`, not by this run).
