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

| Capability | Figma / Canva / Photoshop | Engine status | Priority | Notes |
|---|---|---|---|---|
| Fixed canvas presets | Frame presets / Custom size / New document | have | — | `square` 1080×1080, `portrait` 1080×1350, `story` 1080×1920, `landscape` 1920×1080 |
| A4 print size | Canva/Photoshop A4 | **have (new)** | — | `a4` 1240×1754 = A4 at 150 ppi; `export --scale 2 --dpi 300` gives 2480×3508. Every theme with a portrait layout comes in A4 and uses the portrait arrangement unless it declares its own `a4` entry. Not offered by `compose` or the V1B brief adapter yet |
| Arbitrary canvas size, A3/A5/US Letter | yes | missing | P2 | each size needs a fallback or its own layout entries |
| Resize one design to several sizes | Canva Magic Resize, Figma duplicate frame | **have (new)** | — | `"variants": [...]` on a batch item expands to one item per registered size (`expand_variants`) |
| Templates | thousands | partial (14) | P3 | data-defined themes (`graphic-theme.v1`); `book-production graphics themes` lists them |
| Brand kit colours | Brand Kit / Styles | have | — | primary, accent, foreground, background tokens |
| Brand fonts | Brand Kit fonts | partial | P2 | one custom TTF/OTF per item via `font_ref`; no per-layer font roles |
| Logo slot | Brand Kit logo | partial | P2 | `graphics compose --logo` and campaign plans place the logo in a clear corner (with a plate when contrast is low); fixed themes have no logo slot |
| Text: family, size, colour, alignment | yes | have | — | mirrored 1:1 in the Figma guide |
| Text auto-fit (shrink to fit, never truncate) | Canva auto-resize, Figma plugins | have | — | `fit_text` steps the size down to a template minimum, rejects overflow |
| Thai cluster-safe line breaking | partial in all three | have | — | vowels/tone marks never start a line; guide types the engine's exact lines |
| Font weight | yes | have | — | `normal` / `bold` / `black` per text node and per styled run |
| Italic | yes | missing | P3 | |
| Line height / letter spacing control | yes | have | — | `line_height` and `letter_spacing` per text node |
| Rich text (mixed styles in one box) | yes | missing | P3 | |
| Shapes | rect, ellipse, polygon, vector | have | — | `graphic-ir.v2`: rect with corner radius, ellipse, path, line, linear/radial gradients |
| Image placement | place / frame | have | — | contain-fit (`preserveAspectRatio meet`), local files only, unsafe SVG rejected |
| Image crop/cover, masks, filters | yes | have | — | `fit: cover/contain`, focus point, cut-out, shadow, filters (image effects) |
| Background removal | Canva/Photoshop AI | partial | — | optional `cutout` extra (rembg on CPU) or the built-in plain cut-out |
| Editable output with named layers | `.fig`, `.psd` | have | — | SVG with node ids; the manual guide rebuilds the same layer tree in Figma |
| Groups / components / auto layout | yes | missing | P3 | |
| Export PNG 1x | yes | have | — | per item |
| Export PNG @2x–6x | yes | have | — | `graphics export --scale` |
| Export SVG | Figma/Photoshop | have | — | the batch source of truth |
| Export PDF | yes | partial | P2 | raster PDF at a chosen DPI (`graphics export --dpi`); no vector PDF |
| Export JPEG / WebP | yes | missing | P2 | |
| Print: bleed, crop marks, CMYK | Photoshop / InDesign / Canva Print | **have (new)** | — | `graphics export --bleed-mm 3 --crop-marks --cmyk-icc <printer profile>`: TrimBox/BleedBox/MediaBox, mirrored bleed, registration-colour crop marks, sRGB→CMYK (relative colorimetric + BPC) with the profile as output intent. No profile is bundled; not validated as PDF/X (`pdfx_claimed: false`) |
| Spot colours, overprint, PDF/X-1a/X-4 validation | InDesign / Acrobat Preflight | missing | P2 | needs a preflight tool (veraPDF or Acrobat) in the loop |
| Accessibility contrast check | plugins / Canva checker | **have (new, advisory)** | — | WCAG AA per text layer in the trace QA step and the guide checks; never rejects |
| Bulk create from data | Canva Bulk Create, Figma plugins | have | — | batch JSON / HTTP API, up to 100 items, idempotent replay |
| History, provenance, redo by hand | version history | **have (new)** | — | `trace.json`, `history.jsonl`, `MANUAL-STEPS.md` (Figma steps, Canva/Photoshop alternatives) |
| Collaboration, comments | yes | — | — | not an editor |
| Animation / video | Canva, Figma prototypes | — | — | |

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

1. P2 — A4 in `compose` (per-archetype tables) and in the V1B brief adapter (needs eval cases).
2. P2 — Preflight the press PDF with veraPDF or Acrobat and claim PDF/X only when it passes.
3. P2 — Logo slot in the fixed themes.
4. P2 — JPEG/WebP export; vector PDF via the browser's print-to-PDF.
5. P2 — More print sizes (A3, A5, US Letter) through the same fallback.
6. P3 — Italic, rich text beyond styled runs.

## A4 and press PDF (this pass)

- `a4` is a registered size. `parse_theme` adds it to every theme that has `portrait`;
  a `by_variant` table or `when.variant` without an `a4` entry uses the portrait one.
  All 14 themes compile and pass QA at A4, and a visual check matched portrait.
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
