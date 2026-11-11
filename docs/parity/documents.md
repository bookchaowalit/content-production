# Documents V1A — feature parity with Microsoft Word, Google Docs, and Adobe Acrobat

Scope: `services/document-production-api` (`document_production`) +
`packages/document-production-ir` (tool id `documents-v1a`; V1B brief adapter
and V1C scan import included). The engine is a deterministic batch renderer:
a validated request goes through a pinned Decimal recipe into DOCX (python-docx)
and PDF (ReportLab), then QA. It is not a word processor or a PDF editor, so
editing features are out of scope (—), not missing.

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope for a deterministic batch engine (or already have).

Before this pass (service `docs/PARITY.md`, Word + invoicing software only):
9 have, 1 partial, 9 missing, no P1 labels. Now: see the counts under the table.

## Capability matrix

| Capability | Word / Google Docs / Acrobat | Engine status | Priority | Notes | Flow |
|---|---|---|---|---|---|
| DOCX + PDF from structured data | Save as / Download / Create PDF | have | — | one request → `document.docx` + `document.pdf`, atomic writes, sha256 per artifact | LOGIC |
| Document templates | template galleries | partial (6) | P3 | `invoice@1`, `quotation@1`, `proposal@1`, `transcript@1`, `report@1`, `spec-sheet@1`; fixed layouts, no user templates | LOGIC |
| Paragraph styles: Title, Heading 1, Normal | Styles gallery | have | — | real Word style names in the DOCX (screen readers and the navigation pane see the headings) | LOGIC |
| Style look (fonts/colours/spacing of each style), brand theme | Design ▸ Themes, Docs styles | partial | P2 | DOCX `Normal` = Sarabun (**new**); Title/Heading look comes from python-docx's template, so Word 365 and Writer show their own built-ins (see VERIFY) | LOGIC |
| Multi-level headings, heading numbering, table of contents | multilevel list, References ▸ Table of Contents, Docs TOC | missing | P3 | one heading level per section today | LOGIC |
| Letterhead: seller name, address, tax ID, logo | header with logo | missing | P2 | a Thai full tax invoice (§86/4) needs seller and buyer tax IDs, so the engine does not claim "ใบกำกับภาษี" | LOGIC |
| Page setup A4 + margins | Layout ▸ Size / Margins | **have (new)** | — | PDF was A4; the DOCX was US Letter until this pass (found by the reverse guide run) | LOGIC |
| Footer with document id + page number | Insert ▸ Page Number | have | — | DOCX PAGE field and PDF canvas footer, every page | LOGIC |
| "Page X of Y", header text | NUMPAGES field, Docs page count | missing | P3 | | LOGIC |
| Tables (Table Grid), header row repeated on every PDF page | Insert ▸ Table, Repeat Header Rows | have | — | ≤ 100 line items, multi-page tested | LOGIC |
| Line totals and VAT 7 % per line, Decimal half-up | table formulas `=SUM(ABOVE)` | have | — | `decimal-half-up-v1`; QA re-adds the table and checks grand = subtotal + VAT | LOGIC |
| Withholding tax (ภาษีหัก ณ ที่จ่าย) and net payable | Thai accounting tools (Word/Docs: by hand) | **have (new)** | — | `withholding_tax_rate` 0 < r ≤ 15 on the amount before VAT; `withholding_tax`, `net_payable`; QA checks `net_payable` | LOGIC |
| Amount in Thai words (…บาทถ้วน) | Excel `BAHTTEXT`; Word and Docs have no built-in | **have (new)** | — | `thai_text.baht_text` for THB + `th*` documents, in DOCX, PDF, QA and the Word guide | LOGIC |
| Discounts, deposits, rounding adjustments | invoicing tools | missing | P2 | | LOGIC |
| Running document number, status draft/issued/void | invoicing tools | missing | P2 | `document_id` is supplied by the caller | LOGIC |
| Watermark (DRAFT / COPY) | Design ▸ Watermark, Acrobat Edit PDF ▸ Watermark | missing | P2 | | LOGIC |
| Thai fonts embedded in the PDF | Word/Docs embed on export | have | — | one TTF covering Latin, digits and every Thai code point with real glyphs; a configured incomplete font fails loudly | LOGIC |
| Thai line breaking in the DOCX | Word, Writer and Docs break Thai by dictionary | have | — | the DOCX carries plain text; the program that opens it breaks lines | LOGIC |
| Thai cluster-safe line breaking in the PDF | done by the program on export | **have (new)** | — | `thai_text.wrap_lines`: never splits a cluster (no leading vowel at a line end, no tone mark/following vowel at a line start), fits the real font width | LOGIC |
| Thai dictionary (word) line breaking in the PDF | Word, Docs | partial | P2 | breaks between clusters, not between words; a dictionary tokenizer (PyThaiNLP) would be a new dependency | LOGIC |
| Images with hash check and captions | Insert ▸ Pictures | have | — | `report@1` / `spec-sheet@1`; image re-hashed right before rendering | LOGIC |
| Alt text, document language, accessibility check | Word Accessibility Checker, Acrobat Accessibility | partial | P2 | headings are real styles; no image alt text, no `w:lang`, no checker run | LOGIC |
| Tagged PDF | Word "Document structure tags", Acrobat Autotag | missing | P2 | ReportLab output is untagged | LOGIC |
| PDF/A archival PDF | Word "PDF/A compliant", Acrobat Save as PDF/A | missing | P2 | needs output intent + XMP and a validator (veraPDF) in the loop | LOGIC |
| Batch / mail merge from data | Word Mailings, Docs add-ons, Acrobat Action Wizard | partial | P2 | one job per request over CLI/HTTP with idempotent replay, plus `scripts/benchmark` over a fixture list; no CSV merge command | LOGIC |
| Digital signature or signature placeholder | Insert ▸ Signature Line, Acrobat Certificates / Fill & Sign | missing | P2 | no signature line, no PAdES signing | LOGIC |
| Fillable form fields | content controls, Acrobat Prepare Form | missing | P3 | output documents are final, not forms | LOGIC |
| Scan / OCR import → field proposals | Acrobat Scan & OCR, Docs "Open with Google Docs" OCR | partial | P2 | PDF text, DOCX and images with page evidence; OCR runs only where Poppler + Tesseract (`tha`) are installed, otherwise `ocr_unavailable` → `needs_input`; proposals only, never renders | LOGIC |
| Track changes, comments | Review ▸ Track Changes, Docs suggesting mode | — | — | not an editor; every job is a new immutable version in `history.jsonl` | — |
| Real-time co-editing | Word/Docs co-authoring | — | — | not an editor | — |
| Export DOCX and PDF | Save as / Download / Export | have | — | | LOGIC |
| Export ODT, HTML, Markdown, plain text | Save as / Download as | missing | P3 | | LOGIC |
| Brief → request fields (customer, date, items; never totals) | Copilot in Word, Gemini in Docs, Acrobat AI Assistant | have (shadow; live by owner approval) | — | `ProviderBriefAdapter` through `brief_adapter_kit` → Central AI Gateway; budget caps, ≤ 2 calls/job; fallback `RuleBasedExtractor` (0 calls) | AI-ASSIST |
| Ground the AI proposal against the brief, reject model totals | — | have | — | `validate_extraction_result` + grounding: values must appear in the brief, `subtotal`/`grand_total` from a model are refused | LOGIC |
| Fill fields the brief or scan left missing/ambiguous | — | have | — | `needs_input` with field names; the owner supplies them and resubmits | HUMAN-GATE |
| QA: hashes, page count, critical text in DOCX and PDF, totals arithmetic | Acrobat Preflight (partly) | have | — | now also the amount-in-words line and `net_payable` | LOGIC |
| History, trace, replay by idempotency key | version history | have | — | `trace.json`, `history.jsonl`, `production_trace verify` | LOGIC |
| Make it by hand (reverse guide) | — | **have (new)** | — | [docs/reverse/documents-v1a/word.md](../reverse/documents-v1a/word.md), followed in LibreOffice Writer ([VERIFY.md](../reverse/documents-v1a/VERIFY.md)) | LOGIC |

Flow column: `LOGIC` rules/code (repeatable), `AI-ASSIST` a model proposes through the AI gateway and a `LOGIC` step validates it, `HUMAN-GATE` a person decides, `—` out of scope. OCR in scan import is local Tesseract with fixed settings and no gateway call, so it is `LOGIC`. See [the flow spec](../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md) and `document_production/flow.py`.

Counts now: 19 have, 6 partial, 11 missing, 2 out of scope (—); open P1: 0.

## Implemented in this pass

Spec: [2026-11-12-documents-parity.md](../superpowers/specs/2026-11-12-documents-parity.md).

1. **Amount in Thai words** (`thai_text.baht_text`, `renderers.amount_in_words_line`):
   `grand_total (ตัวอักษร): (…บาทถ้วน)` after the totals of every THB document in a
   `th*` locale, in DOCX, PDF, QA and the Word guide. Tests:
   `tests/test_thai_text.py::test_baht_text_reads_thai_amounts` (15 readings),
   `test_baht_text_rejects_negative_amounts`,
   `tests/test_thai_totals.py::test_amount_in_words_only_for_thai_baht_documents`,
   `test_qa_requires_the_amount_in_words_line`.
2. **Withholding tax** (`withholding_tax_rate` on `document-request.v1`, optional;
   unset requests dump and hash exactly as before): `withholding_tax` and
   `net_payable` in `derived_totals`, QA `net_payable_mismatch`. Tests:
   `tests/test_thai_totals.py::test_withholding_tax_is_on_the_amount_before_vat`,
   `test_documents_without_withholding_keep_the_old_totals`,
   `test_withholding_rate_outside_thai_rates_is_rejected`,
   `test_docx_and_pdf_carry_withholding_and_amount_in_words`,
   `test_qa_flags_a_net_payable_that_does_not_add_up`,
   `test_word_guide_has_the_withholding_and_words_steps`;
   `packages/document-production-ir/tests/test_models.py::test_withholding_rate_*`.
3. **Thai cluster-safe PDF line breaking** (`thai_text.wrap_lines`, used for every
   Thai paragraph and table cell in the PDF). Tests:
   `tests/test_thai_text.py::test_wrap_lines_*`,
   `test_pdf_breaks_a_long_thai_note_between_clusters`.
4. **Reverse guide fixes** (A4 DOCX, Sarabun in the DOCX `Normal` style, side
   margins in the guide): `tests/test_renderers.py::test_docx_page_is_a4_like_the_pdf_and_the_word_guide`,
   `test_docx_declares_the_thai_font_the_word_guide_names`.
5. **Flow + reverse guide**: `document_production/flow.py`,
   `tests/test_reverse_guide.py::test_word_reverse_guide_matches_engine`.

## Roadmap (prioritised)

1. P2 — Letterhead and seller/buyer tax IDs, so the invoice template can be a
   §86/4 full tax invoice; running numbers and draft/issued/void status.
2. P2 — Discounts and deposits in the recipe (QA arithmetic extended the same way as withholding).
3. P2 — Accessibility: image alt text, `w:lang` = th-TH, tagged PDF; then PDF/A with veraPDF.
4. P2 — Signature line placeholder in the DOCX and a signature field in the PDF.
5. P2 — CSV batch command (mail merge) over the existing idempotent job API.
6. P2 — OCR runtime (Poppler + Tesseract `tha`) in the deployment image to close V1C.
7. P2 — Pin the Title/Heading look in the DOCX and the guide; dictionary Thai word breaking in the PDF.
8. P3 — TOC and numbered headings, Page X of Y, more export formats, form fields.
