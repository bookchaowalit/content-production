# web-v1a — reverse guide verification

Rule: [reverse guides spec R4](../../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md).

| Date | Guide | Program | Followed by | Method | Result |
|---|---|---|---|---|---|
| 2026-11-12 | [webflow.md](webflow.md) | Plain HTML/CSS, the guide's own alternative ("VS Code — HTML/CSS ล้วน"), rendered in headless Chromium (chrome-headless-shell, Playwright build 1248) | Claude agent with [`evidence/follow_guide.py`](evidence/follow_guide.py): it reads only the committed guide (layer table + 134 numbered steps) and turns each step into one DOM or CSS operation with the guide's exact values, using the Webflow Style-panel vocabulary (combo class `.button.is-hero`, `All H1 Headings`, `faq-question`, `form-field` Div, Tablet / Mobile landscape breakpoints) | Engine page from `python -m web_production.cli build --input examples/web/landing-th.json --asset-root examples/web` vs the hand-built page; full-page screenshots at 375 / 768 / 1440 px wide; `scripts/reverse_image_diff.py`, threshold 16/255 | **Pass** (0.00 % of pixels differ at all three widths; the PNGs are byte-identical) |
| — | [webflow.md](webflow.md) | Webflow Designer (web) | not run: Webflow is a hosted editor and is not runnable on this machine | — | **Pending owner** (steps below) |

**What the 2026-11-12 run proves.** It verifies the guide's *values* (structure,
text, links, colours, spacing, type, grid columns, both breakpoints, the form)
with browser defaults. It does **not** verify Webflow's UI, Webflow's own base
stylesheet (`webflow.css`), its `w-` classes, or what Webflow's export writes.

## 2026-11-12 run

Engine artifacts match the goldens in `webflow.md`: HTML `5b663e01…1f1f`, CSS
`0563db07…ec51`, hero PNG `ac9d5fac…d5c`. The hand-built files are in
[`evidence/manual-build/`](evidence/manual-build/) with the step log
([`follow-log.txt`](evidence/manual-build/follow-log.txt)). Their bytes differ
from the engine's (Webflow-style selectors such as `.button.is-hero` instead of
`.hero .button`, one rule per guide step), but the rendered pages are equal.

| Width | Pixels differing (> 16/255) | Evidence |
|---|---|---|
| 375 px (mobile, ≤767 rules) | 0.00 % | [engine](evidence/engine-375.png) · [hand-built](evidence/manual-375.png) · [diff](evidence/diff-375.png) |
| 768 px (tablet, ≤991 rules) | 0.00 % | [engine](evidence/engine-768.png) · [hand-built](evidence/manual-768.png) · [diff](evidence/diff-768.png) |
| 1440 px (desktop) | 0.00 % | [engine](evidence/engine-1440.png) · [hand-built](evidence/manual-1440.png) · [diff](evidence/diff-1440.png) |

The first run of the same guide differed by 6.70 % / 4.72 % / 3.94 %. Three
of the causes were guide errors, fixed in `web_production/manual.py`. Two were
bugs in the follower script.

### Findings fixed in the guide

1. **border-box.** The guide never said that padding and borders count inside
   the width. The form inputs and the CTA box came out wider than the
   container. New step: `* { box-sizing: border-box }` (Webflow already sets
   this in `webflow.css`; the plain HTML/Framer path needs it).
2. **Submit button stretched.** The guide put the Submit Button straight into
   the grid Form, so it filled the row. It is now wrapped in a `form-field`
   Div, like the engine's `<p>`.
3. **Webflow tag defaults (found by review, not measured).** Webflow's base
   stylesheet sets body text to 14px and gives h2/h3/p/ul their own sizes and
   margins. The engine relies on browser defaults. New steps set Body to 16px
   and `All H2/H3 Headings`, `All Paragraphs` and `All Lists` to the browser
   values. In plain HTML these steps change nothing, as the 0.00 % shows. In
   Webflow they are still unverified (see Pending owner).

Follower bugs (not guide errors): the font-size `1.125rem (18px)` was read
with the `(18px)` hint attached, and the `faq-question` class was put on the
wrong `summary`.

### Not covered by this run

- Webflow itself (below); Framer and WordPress alternatives.
- Keyboard focus rings and the `<details>` open state (not visible in a static screenshot); they are checked by QA (`focus_visible`, contrast pairs) and unit tests.
- The `en` layout (`line-height: 1.05` on the hero heading), pages without a form or image.

## Pending owner: follow the guide in Webflow

1. Log in to Webflow (a free Starter site is enough), `New site ▸ Blank site`, open the Designer.
2. Follow [webflow.md](webflow.md) steps 1–130 exactly, uploading `examples/web/hero.png` at step 36. Type the copy from the layer table.
3. Publish to the free `*.webflow.io` subdomain (or use `Export code` if the plan allows it).
4. Screenshot the published page at 375, 768 and 1440 px wide (full page, device scale 1). Build the engine page with `python -m web_production.cli build --input examples/web/landing-th.json --output /tmp/web --idempotency-key owner-1 --asset-root examples/web` and screenshot it the same way.
5. Run `uv run python scripts/reverse_image_diff.py ENGINE.png WEBFLOW.png --json` per width. Add a row here with the measured percentages and any guide fixes. Expect small differences from Webflow's base styles and fonts. Any difference in layout, colours, text or breakpoints is a guide bug.
