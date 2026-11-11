# Graphics V1A — feature parity with Figma, Canva, and Photoshop

Scope: `graphics_production` + `graphics_ir` (tool id `graphics-v1a`). The
engine is a deterministic template compiler: it fills fixed templates with
brand tokens and text, lays text out with Thai-safe wrapping, writes an
editable SVG, and rasterises it through a local Chromium. It is not a
free-form design editor, so "missing" below often means "not a template
feature yet", not "impossible".

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope for a deterministic batch engine.

## Capability matrix

| Capability | Figma / Canva / Photoshop | Engine status | Priority | Notes | Flow |
|---|---|---|---|---|---|
| Fixed canvas presets | Frame presets / Custom size / New document | have | — | `square` 1080×1080, `portrait` 1080×1350, `story` 1080×1920, `landscape` 1920×1080 | LOGIC |
| A4 print size | Canva/Photoshop A4 | **have (new)** | — | `a4` 1240×1754 = A4 at 150 ppi; `export --scale 2 --dpi 300` gives 2480×3508. The 11 `graphic-ir.v2` themes (laid out in W/H/MIN) come in A4 and use the portrait arrangement unless they declare an `a4` entry; the three `graphic-ir.v1` themes (`quote_card`, `product_card`, `announcement`, fixed 1080 px coordinates) do not. Not offered by `compose`, campaigns, or the V1B brief adapter yet | LOGIC |
| Arbitrary canvas size, A3/A5/US Letter | yes | missing | P2 | each size needs a fallback or its own layout entries | LOGIC |
| Resize one design to several sizes | Canva Magic Resize, Figma duplicate frame | **have (new)** | — | `"variants": [...]` on a batch item expands to one item per registered size (`expand_variants`) | LOGIC |
| Templates | thousands | partial (14) | P3 | data-defined themes (`graphic-theme.v1`); `book-production graphics themes` lists them | LOGIC |
| Brand kit colours | Brand Kit / Styles | have | — | primary, accent, foreground, background tokens | LOGIC |
| Brand fonts | Brand Kit fonts | partial | P2 | one custom TTF/OTF per item via `font_ref`; no per-layer font roles | LOGIC |
| Logo slot | Brand Kit logo | partial | P2 | `graphics compose --logo` and campaign plans place the logo in a clear corner (with a plate when contrast is low); fixed themes have no logo slot | LOGIC |
| Text: family, size, colour, alignment | yes | have | — | mirrored 1:1 in the Figma guide | LOGIC |
| Text auto-fit (shrink to fit, never truncate) | Canva auto-resize, Figma plugins | have | — | `fit_text` steps the size down to a template minimum, rejects overflow | LOGIC |
| Thai cluster-safe line breaking | partial in all three | have | — | vowels/tone marks never start a line; guide types the engine's exact lines | LOGIC |
| Font weight | yes | have | — | `normal` / `bold` / `black` per text node and per styled run | LOGIC |
| Italic | yes | missing | P3 | | LOGIC |
| Line height / letter spacing control | yes | have | — | `line_height` and `letter_spacing` per text node | LOGIC |
| Rich text (mixed styles in one box) | yes | missing | P3 | | LOGIC |
| Shapes | rect, ellipse, polygon, vector | have | — | `graphic-ir.v2`: rect with corner radius, ellipse, path, line, linear/radial gradients | LOGIC |
| Image placement | place / frame | have | — | contain-fit (`preserveAspectRatio meet`), local files only, unsafe SVG rejected | LOGIC |
| Image crop/cover, masks, filters | yes | have | — | `fit: cover/contain`, focus point, cut-out, shadow, filters (image effects) | LOGIC |
| Background removal | Canva/Photoshop AI | partial | — | optional `cutout` extra (rembg on CPU) or the built-in plain cut-out | LOGIC |
| Editable output with named layers | `.fig`, `.psd` | have | — | SVG with node ids; the manual guide rebuilds the same layer tree in Figma | LOGIC |
| Groups / components / auto layout | yes | missing | P3 | | LOGIC |
| Export PNG 1x | yes | have | — | per item | LOGIC |
| Export PNG @2x–6x | yes | have | — | `graphics export --scale` | LOGIC |
| Export SVG | Figma/Photoshop | have | — | the batch source of truth | LOGIC |
| Export PDF | yes | partial | P2 | raster PDF at a chosen DPI (`graphics export --dpi`); no vector PDF | LOGIC |
| Export JPEG / WebP | yes | missing | P2 | | LOGIC |
| Print: bleed, crop marks, CMYK | Photoshop / InDesign / Canva Print | **have (new)** | — | `graphics export --bleed-mm 3 --crop-marks --cmyk-icc <printer profile>`: TrimBox/BleedBox/MediaBox, mirrored bleed, registration-colour crop marks, sRGB→CMYK (relative colorimetric + BPC) with the profile as output intent. No profile is bundled; not validated as PDF/X (`pdfx_claimed: false`) | LOGIC |
| Spot colours, overprint, PDF/X-1a/X-4 validation | InDesign / Acrobat Preflight | missing | P2 | needs a preflight tool (veraPDF or Acrobat) in the loop | LOGIC |
| Accessibility contrast check | plugins / Canva checker | **have (new, advisory)** | — | WCAG AA per text layer in the trace QA step and the guide checks; never rejects | LOGIC |
| Bulk create from data | Canva Bulk Create, Figma plugins | have | — | batch JSON / HTTP API, up to 100 items, idempotent replay | LOGIC |
| History, provenance, redo by hand | version history | **have (new)** | — | `trace.json`, `history.jsonl`, `MANUAL-STEPS.md` (Figma steps, Canva/Photoshop alternatives) | LOGIC |
| Collaboration, comments | yes | — | — | not an editor | — |
| Animation / video | Canva, Figma prototypes | — | — | | — |
| Design from a text brief | Canva Magic Design, Figma First Draft | have (shadow; live by owner approval) | — | grounded by `ground_proposal`: every slot text must appear in the brief; eval sets in `brief-shadow` | AI-ASSIST |
| Edit by plain-language instruction | Canva Magic Edit, Figma AI | have (shadow) | — | `graphics edit` → `design_edit.apply_change` validates the change; fallback = `design` overrides by hand | AI-ASSIST |
| Art direction / layout proposal | Canva Magic Design | have | — | AI director v2 9/10 compose eval (score 94.5); deterministic composer is the default | AI-ASSIST |
| Approve / pick options, feedback loop | comments, approvals | have | — | `graphics review` page + `feedback`; verdicts teach the composer | HUMAN-GATE |
| Make it by hand (reverse guide) | — | **have (new)** | — | [docs/reverse/graphics-v1a/figma.md](../reverse/graphics-v1a/figma.md), verified in Figma ([VERIFY.md](../reverse/graphics-v1a/VERIFY.md)) | LOGIC |

Flow column: `LOGIC` rules/code (repeatable), `AI-ASSIST` a model proposes through the AI gateway and a `LOGIC` step validates it, `HUMAN-GATE` a person decides, `—` out of scope. Background removal runs a local model (rembg) with no gateway call and is treated as `LOGIC` (same input, same output). See [the flow spec](../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md).

## Implemented in this pass

1. **Multi-size resize** (`expand_variants`): one item with
   `"variants": ["square", "portrait", "story"]` becomes three items with the
   registered canvas sizes and `request_id-<variant>` names. Ambiguous input
   (both `variant` and `variants`, duplicates, non-strings) is left unchanged
   so the request validator rejects it. Tests:
   `tests/graphics/test_trace_manual.py::test_expand_variants_*`.
2. **WCAG contrast advisory** (`qa.contrast_report`): ratio of every text
   layer against the shape painted behind it, compared with AA (4.5:1, or 3:1
   at ≥ 24 px). Recorded in the trace QA step and listed in the guide checks.
   Advisory only, because existing brand kits (accent on white) would fail.
3. **Traceability and manual steps** for batches and print exports (see the
   README section "Job history, trace & manual steps").

Already present before this pass (so not re-implemented): SVG export, raster
PDF/@Nx print export, text auto-fit.

## Roadmap (prioritised)

Done since the first matrix: font weight, landscape 16:9, logo placement (compose and
campaigns), shapes and gradients, image effects, 14 themes, A4, and the press PDF
(bleed, crop marks, CMYK).

1. P2 — A4 in `compose`/campaigns (per-archetype tables), the V1B brief adapter (needs eval
   cases), and the three v1 themes (convert their pixel coordinates to W/H expressions).
2. P2 — Preflight the press PDF with veraPDF or Acrobat and claim PDF/X only when it passes.
3. P2 — Logo slot in the fixed themes.
4. P2 — JPEG/WebP export; vector PDF via the browser's print-to-PDF.
5. P2 — More print sizes (A3, A5, US Letter) through the same fallback.
6. P3 — Italic, rich text beyond styled runs.

## A4 and press PDF (this pass)

- `a4` is a registered size. `parse_theme` adds it to every `graphic-ir.v2` theme that has
  `portrait` (v1 themes use fixed pixels and would sit off-centre on the wider page);
  a `by_variant` table or `when.variant` without an `a4` entry uses the portrait one.
  The 11 A4 themes compile and pass QA, every text anchor sits at the same fraction of the
  width as in portrait (test), and a visual check matched portrait. Campaign and compose
  `all` stay on the screen sizes.
  `make --variant all` still means the screen sizes; ask for `--variant a4` by name.
- `print_pdf.press_pdf` writes the PDF itself so it can set the print boxes. Bleed is
  mirrored from the artwork edge (the SVG has nothing beyond its canvas). CMYK needs the
  printer's ICC profile (`--cmyk-icc`); an RGB or unreadable profile is refused.
- Tests: `tests/graphics/test_print_and_a4.py` (browser-free; the real CMYK conversion runs
  when a CMYK profile is found or `GRAPHICS_TEST_CMYK_ICC` is set).

## Runtime notes

PNG rasterisation needs a local Chromium-family browser. In environments
without one, rendering tests skip; trace/guide tests replace the rasteriser
with a canvas-sized PNG and therefore verify structure, not pixels.
