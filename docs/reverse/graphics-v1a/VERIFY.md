# graphics-v1a — reverse guide verification

Rule: [reverse guides spec R4](../../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md).

| Date | Guide | Program | Followed by | Method | Result |
|---|---|---|---|---|---|
| 2026-11-12 | [figma.md](figma.md) | Figma Design (web), team starter plan, draft file | Claude agent via the Figma Plugin API (each guide step as one API call: Frame, Rectangle, Text with the guide's X/Y/W/H, fill, font size, line height px, letter spacing 0 %, align) | Engine PNG from `book-production graphics batch --input examples/graphics/batch.json` (headless Chromium) vs Figma 1x export; `scripts/reverse_image_diff.py`, threshold 16/255 | **Pass with tolerance** (below) |

## 2026-11-12 run

Evidence: [engine](evidence/engine-demo-announcement.png) ·
[Figma](evidence/figma-demo-announcement.png) ·
[diff mask](evidence/diff-demo-announcement.png). The engine SVG sha256
matches the golden in `figma.md` (`acb93f47…b17a`).

| Region | Pixels differing (> 16/255) |
|---|---|
| whole canvas | 1.92 % |
| accent bar, blank background, CTA button edge | 0.00 % |
| headline (80 px) | 4.57 % |
| body (42 px) | 2.21 % |
| CTA label (32 px) | 9.44 % |

Ink bounding boxes of each text layer: same width and height within 3 px,
horizontal offset ≤ 2 px, vertical offset 7 / 3 / 2 px for 80 / 42 / 32 px
type (≈ 0.09 × font size). Shapes and colours are exact.

### Findings fixed in the guide

1. **Font.** The guide said Arial. Figma (web) has no Arial, and Arial has no
   Thai glyphs, so the engine's Thai text is drawn by the browser fallback,
   `Sarabun`. The Figma guide now says to use `Sarabun / Regular` wherever it
   says Arial (`manual.THAI_FALLBACK_FAMILY`).
2. **Baseline.** Figma puts the first baseline of a fixed text box slightly
   lower than an SVG `<tspan>`. The overlay check now allows a vertical
   difference of up to 0.1 × font size (`manual.FIGMA_TEXT_Y_TOLERANCE`) and
   still requires exact shapes, colours, sizes and horizontal position.

### Not covered by this run

- Themed (`graphic-ir.v2`) items, whose guide targets Affinity Designer 2.
- Canva and Photoshop alternatives listed in the guide.
- The print-export guide (`export.build_export_guide`).
