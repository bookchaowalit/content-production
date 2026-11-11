# Web V1A — feature parity with Webflow, Framer, and Carrd

Scope: `services/web-production-api` (`web_production`) + `packages/web-production-ir`
(`web_ir`), tool id `web-v1a`, recipe `landing-template-v4`. The engine is a
deterministic, single-page, **no-JavaScript** landing-page renderer: a fixed
template (hero, features, optional pricing, FAQ and lead form, CTA, footer)
filled from a validated `PageRequest`, written as one HTML file, one CSS file
and an optional bundled hero image. It is not a visual site editor and has no
hosting, so many "missing" rows are by design.

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope for a deterministic static-page engine.

## Capability matrix

| Capability | Webflow / Framer / Carrd | Engine status | Priority | Notes | Flow |
|---|---|---|---|---|---|
| Section / block library | Webflow layouts + components, Framer sections, Carrd sections + elements | partial (7 fixed sections) | P2 | hero, features (≤6), pricing (1–3 plans), FAQ (≤8, `<details>`), lead form, CTA, footer; no logos, team, stats, or custom blocks | LOGIC |
| Testimonials | all three | missing | P2 | needs a structured, verbatim quote source; the AI adapter must never write a quote. Not needed for a valid, accessible page, so not P1 | LOGIC |
| Image gallery / several images | all three | missing | P2 | one hero image only | LOGIC |
| Navigation menu, multi-page site | all three | missing | P2 | one page per job | LOGIC |
| Responsive breakpoints | Webflow 991/767/478 px (+ large), Framer custom, Carrd mobile layout | have | — | `@media` 991 px (Tablet) and 767 px (Mobile landscape), same as Webflow; QA requires both; verified at 375 / 768 / 1440 px ([VERIFY.md](../reverse/web-v1a/VERIFY.md)) | LOGIC |
| Typography: fluid hero heading, type scale | all three | have | — | `clamp(2.25rem, 7vw, 5rem)` hero `h1`, 1.125 rem hero body, body line height 1.6 | LOGIC |
| Thai line height | partial (manual setting) | **have (new)** | — | `th` hero heading `line-height: 1.35` (Latin 1.05); QA `thai_line_height_too_tight` rejects any `line-height` < 1.3 on a `th` page | LOGIC |
| Custom / web fonts | all three | missing | P2 | system font stack only (no external font request by design); a bundled font file would need the hero-image mechanism | LOGIC |
| Theme colours | Webflow variables, Framer styles, Carrd colours | have | — | primary / background / foreground; derived colours pre-computed in the guide | LOGIC |
| Lead / contact form (static markup) | Webflow Forms, Framer Forms, Carrd Form element | **have (new)** | — | `contact_form`: 1–4 fields from name/email/phone/message (needs email or phone), visible labels, `required`, `autocomplete`, POST to a relative path; QA `form_action_unsafe`, `form_label_missing` | LOGIC |
| Form submission backend, spam filter, notifications | built in (hosted) | — | — | no backend by design; whoever hosts the files answers the POST (flow part 10) | HUMAN-GATE |
| Title + meta description | all three | have | — | QA-enforced | LOGIC |
| Open Graph / Twitter card | all three | partial | P2 | `og:type/title/description/locale`, `twitter:card`; no `og:image` (needs an absolute URL on a known domain) | LOGIC |
| Structured data (JSON-LD) | Webflow / Framer custom code, Carrd embed | missing | P2 | needs a QA exception for `application/ld+json` only | LOGIC |
| Canonical URL, sitemap.xml, robots.txt | Webflow, Framer (auto); Carrd Pro | missing | P2 | needs a validated `site_url` | LOGIC |
| Landmarks, one H1, image alt, safe links | all three (audit) | have | — | QA-enforced (`required_landmarks`, `single_h1`, `image_alt`, `safe_href`) | LOGIC |
| WCAG 2.2 AA colour contrast | Webflow Audit panel (warns), Framer/Carrd none | **have (new, blocking)** | — | `qa.contrast_report`: body, hero, button, hero button, CTA text ≥ 4.5:1; failing theme → `contrast_<pair>_insufficient`, page rejected | LOGIC |
| Keyboard focus visible | all three | have | — | `:focus-visible` rings now use contrast-checked colours (foreground, white in the hero); the old yellow `#FBBF24` failed WCAG 1.4.11 on white (1.67:1) | LOGIC |
| Performance: no blocking JS, few requests | Webflow/Framer ship JS runtime; Carrd light | have | — | zero JavaScript, one CSS file, no web fonts, no external assets (QA bans `<script>`, `url()`, `@import`) | LOGIC |
| Image optimisation (srcset, WebP/AVIF, width/height) | Webflow responsive images, Framer CDN, Carrd compression | missing | P2 | the hero image is shipped as given (hash-checked); no resize, no intrinsic size attributes | LOGIC |
| Favicon | all three | missing | P2 | needs a bundled icon file like the hero image | LOGIC |
| Analytics hook | integrations / custom code | — | — | no script by design; add analytics at the host | — |
| Custom code embed | all three | — | — | QA rejects `<script>` and raw HTML by design | — |
| Export static files | Webflow Export code (paid plans), Carrd Pro download, Framer none | have | — | the bundle is the output: HTML + CSS + image + manifest with sha256 | LOGIC |
| Hosting, custom domain, SSL | all three | — | — | no deploy endpoint by design | — |
| CMS collections / blog | Webflow CMS, Framer CMS | missing | P3 | | LOGIC |
| Interactions / animations | Webflow Interactions, Framer motion, Carrd basic | missing | P3 | no JavaScript by design; CSS-only motion possible later | LOGIC |
| Localization (several locales) | Webflow / Framer localization | partial | P3 | one `th` or `en` page per job | LOGIC |
| Replay-safe builds, hashed artifacts | — | have | — | idempotency key + manifest + `production-trace verify` | LOGIC |
| History, trace, redo by hand | version history | have | — | `trace.json`, `history.jsonl`, `MANUAL-STEPS.md` (Webflow) | LOGIC |
| Page from a text brief | Webflow AI Site Builder, Framer AI | have (shadow; live by owner approval) | — | `ProviderPageAdapter` through `brief-adapter-kit` → AI gateway; registered sections and plain copy only; cannot propose a form, link, colour or image | AI-ASSIST |
| Ground the AI proposal | — | have | — | `ground_page_proposal`: names, features, plans and prices verbatim in the brief; no markup/URL; else `needs_input`; then `validate_request` | LOGIC |
| Switch the AI adapter to live | — | have | — | `brief` refuses any config whose `mode` is not `live`; only the owner sets it after a passing shadow report | HUMAN-GATE |
| Approve copy, host, wire the form endpoint | publish button | have | — | a person approves the text and hosts the files; the engine never publishes | HUMAN-GATE |
| Make it by hand (reverse guide) | — | **have (new)** | — | [docs/reverse/web-v1a/webflow.md](../reverse/web-v1a/webflow.md), followed in plain HTML/CSS with 0.00 % pixel difference ([VERIFY.md](../reverse/web-v1a/VERIFY.md)); Webflow run pending owner | LOGIC |

Flow column: `LOGIC` rules/code (repeatable), `AI-ASSIST` a model proposes through the AI gateway and a `LOGIC` step validates it, `HUMAN-GATE` a person decides, `—` out of scope (reason in Notes). The flow is `web_production/flow.py` (`FLOW`). See [the flow spec](../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md).

## This pass (2026-11-12)

Spec: [2026-11-12-web-parity.md](../superpowers/specs/2026-11-12-web-parity.md). Recipe
`landing-template-v3` → `landing-template-v4`. Output bytes changed, so a key
built under v3 now replays as `idempotency_key_reused_with_different_input`.

1. **WCAG AA contrast QA** (`qa.contrast_ratio`, `qa.contrast_report`). Blocking,
   and checked again on replay. The focus rings moved from yellow to
   contrast-checked colours.
2. **Thai line height** (`render.THAI_HEADING_LINE_HEIGHT`, QA `thai_line_height_too_tight`).
3. **Static lead form** (`web_ir.ContactFormSpec`, `render._contact_form`, QA form checks,
   Webflow `Form Block` steps).
4. **Flow + reverse guide**: `web_production/flow.py`; `docs/reverse/web-v1a/webflow.md`
   built from `examples/web/landing-th.json`; drift test
   `services/web-production-api/tests/test_reverse_guide.py`.
5. **Guide fixes found by following it**: border-box, the submit-button wrapper, and
   Webflow tag defaults (see VERIFY.md).

Tests: `services/web-production-api/tests/test_parity_v4.py` (contrast, Thai line
height, lead form, guide, recipe) and `tests/test_reverse_guide.py`.

Earlier passes (v3: pricing, FAQ, OG meta, Webflow breakpoints) are described in
[services/web-production-api/docs/PARITY.md](../../services/web-production-api/docs/PARITY.md),
which now points here.

## Roadmap (P2 first)

1. `site_url` (https, allowlisted): `og:image`, canonical, sitemap, robots, then JSON-LD (Organization / FAQPage).
2. Hero image: intrinsic width/height attributes and a size budget, then WebP variants with `srcset`.
3. Favicon and a bundled brand font through the same hash-checked asset path.
4. Testimonials from a structured source only; gallery; navigation for multi-page sites.
