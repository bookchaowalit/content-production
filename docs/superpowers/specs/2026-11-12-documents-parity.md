---
title: Documents V1A parity pass (Word / Google Docs / Acrobat)
date: 2026-11-12
status: accepted (parity goal 2026-11-12)
owner: Product owner (Book Dev)
tool: documents-v1a
---

# Documents V1A parity pass

Matrix: [docs/parity/documents.md](../../parity/documents.md). Flow rules:
[reverse guides spec](2026-11-12-reverse-guides-and-flow-labels-design.md).

## Problem

A Thai invoice or quotation from the engine is missing three things every
Thai business document made in Word (or a Thai accounting tool) has:

1. the grand total written in Thai words (`หนึ่งหมื่นหกพันห้าสิบบาทถ้วน`);
2. withholding tax (ภาษีหัก ณ ที่จ่าย 1/2/3/5/10/15 %) and the net amount to pay;
3. Thai line breaking in the PDF that never splits a character cluster. The
   ReportLab renderer breaks a space-free Thai run at any character, so a
   leading vowel (`ใ`) can end a line and a tone mark can start one.

## Decision

### D1 Amount in Thai words (`thai_text.baht_text`)

- Pure function `baht_text(Decimal) -> str`. Rounds half-up to satang, then
  reads baht and satang with the Thai rules: `เอ็ด` for a trailing 1 in a
  number above 9 (`สิบเอ็ด`, `หนึ่งร้อยเอ็ด`), `ยี่สิบ` for 2 in the tens,
  `สิบ` (not `หนึ่งสิบ`) for 1 in the tens, millions repeat (`ล้านล้าน`).
  Zero baht with satang reads only the satang (`ห้าสิบสตางค์`); no satang ends
  in `บาทถ้วน`; zero is `ศูนย์บาทถ้วน`. Negative input is rejected.
- Rendered (DOCX + PDF) after the Totals lines for every `THB` document with
  totals: `grand_total (ตัวอักษร): (<baht_text>)`. Same line in the Word
  guide. QA requires that line in both artifacts.

### D2 Withholding tax (invoice@1, quotation@1)

- `DocumentRequest.withholding_tax_rate` (optional decimal). Allowed 0 < rate ≤ 15
  (Thai WHT rates); otherwise issue `withholding_tax_rate_out_of_range`.
- `withholding_tax = round_half_up(subtotal × rate / 100)` (WHT is on the
  amount before VAT); `net_payable = grand_total − withholding_tax`. Both go
  into `derived_totals` after `grand_total` only when a rate is given, so
  documents without WHT are byte-for-byte the same IR as before.
- QA: when `net_payable` is present, `withholding_tax` must be present and
  `net_payable == grand_total − withholding_tax` (`net_payable_mismatch`).
- Amount in words stays on `grand_total` (the invoice total), as Thai tax
  invoices do.

### D3 Thai cluster-safe PDF line breaking (`thai_text.wrap_lines`)

- Before a Thai string goes into a ReportLab `Paragraph`, the renderer breaks
  it itself into lines that fit the available width (measured with the
  embedded font's real metrics) and joins them with `<br/>`.
- Break opportunities: spaces, and between Thai clusters. A cluster is a base
  character plus the combining marks and following vowels after it, with any
  leading vowel (`เ แ โ ใ ไ`) attached to the consonant after it. Text that
  fits on one line, and every non-Thai string, is unchanged.
- No dictionary word breaking (Word and LibreOffice break between words; we
  break between clusters). That gap stays P2 in the matrix.

## Acceptance criteria

- AC1 `baht_text` matches a table of reference readings (0, 1, 11, 21, 101,
  1,000,000, 16,050.00, 0.50, 1,234,567.89, 10^12) and rejects negatives.
- AC2 A THB invoice's DOCX and PDF both contain the amount-in-words line;
  a USD invoice does not; QA fails (`document_text_missing`) if it is absent.
- AC3 `withholding_tax_rate: 3` on a 15,000 THB + 7 % VAT item gives
  `withholding_tax 450.00`, `net_payable 15600.00`; rate 20 or 0 is rejected;
  QA flags a tampered `net_payable`.
- AC4 `wrap_lines` never starts a line with a Thai combining mark or following
  vowel and never ends one with a leading vowel; every line fits the width;
  joining the lines gives back the input (minus break spaces). A long
  space-free Thai note renders to a PDF whose extracted lines obey the same rule.
- AC5 The committed example (`examples/invoice-wht-th.json`) and the Word reverse
  guide show the new lines; the drift test passes.

## Fixes found while following the guide (R4)

Following the Word guide in LibreOffice Writer
([VERIFY.md](../../reverse/documents-v1a/VERIFY.md)) showed the guide and the
engine DOCX disagreed. Fixed in the engine and the guide builder together:

- D4 The DOCX page is A4 (`Mm(210) × Mm(297)`) like the PDF; python-docx's
  template is US Letter while the guide said A4. The guide now also gives the
  left/right margins (`1.25"`, `DOCX_SIDE_MARGIN_INCHES`).
- D5 The DOCX `Normal` style names `Sarabun` for Latin and complex script
  (`DOCX_FONT`), and the guide has one step that sets the same font. Before,
  the DOCX named no Thai font, so each machine fell back to a different face.
- Tests: `test_docx_page_is_a4_like_the_pdf_and_the_word_guide`,
  `test_docx_declares_the_thai_font_the_word_guide_names` (tests/test_renderers.py).
