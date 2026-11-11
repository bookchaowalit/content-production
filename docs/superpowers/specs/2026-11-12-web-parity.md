---
title: web-v1a parity pass (Webflow / Framer / Carrd)
date: 2026-11-12
status: accepted (owner goal 2026-11-12, parity brief)
tool: web-v1a
---

# web-v1a parity pass

Parity matrix: [docs/parity/web.md](../../parity/web.md). Rules for flow labels,
reverse guides and verification: [reverse guides spec](2026-11-12-reverse-guides-and-flow-labels-design.md).

The engine stays a deterministic, single-page, no-JavaScript template
renderer. This pass closes the three gaps that a landing page from Webflow,
Framer or Carrd would not have and that the engine can close without a
backend, a domain, or an owner decision.

## W1 WCAG 2.2 AA colour contrast (blocking QA)

`qa.contrast_report(css_text)` reads the three theme variables from the
`:root` block the engine writes and returns every text / focus pair the
template paints, with its ratio and the WCAG minimum:

| Pair | Foreground | Background | Minimum |
|---|---|---|---|
| `body_text` | foreground | background | 4.5 |
| `hero_text` | `#FFFFFF` | primary | 4.5 (hero body is 18 px regular) |
| `button` | `#FFFFFF` | foreground | 4.5 |
| `hero_button` | foreground | `#FFFFFF` | 4.5 |
| `cta_text` | foreground | `color-mix(primary 12 %, background)` | 4.5 |

Focus rings (WCAG 1.4.11, 3:1 against the adjacent colour) use colours already
covered by the pairs above: the ring is `foreground` on the page and CTA
backgrounds and `#FFFFFF` inside the hero. The yellow ring `#FBBF24` is
removed because it measures 1.67:1 on white and 2.5:1 on the default primary.

`run_page_qa` adds `contrast_<pair>_insufficient` for each failing pair, so a
low-contrast theme is rejected before any file is kept (and on replay).

## W2 Thai line height

Stacked Thai vowels and tone marks need more leading than Latin text. For
`lang="th"` the hero `h1` uses `line-height: 1.35` (`render.THAI_HEADING_LINE_HEIGHT`);
English keeps `1.05`. QA rule `thai_line_height_too_tight`: on a `th` page no
`line-height` in the CSS may be below `1.3`.

## W3 Static lead form (no backend)

Optional `contact_form` in `PageRequest` / `PageIR`:

```json
{"action": "/api/lead", "fields": ["name", "email", "message"], "submit_label": "ส่งข้อมูล"}
```

- `action` is a relative path (`/…`), never a URL, fragment, `mailto:` or
  `javascript:`; method is always `post`. The exact rendered string (no
  strip) must fully match `^/[A-Za-z0-9._~\-/]*$` and contain no `//`
  (`web_ir.form_action_allowed`), because browsers drop tab/CR/LF and edge
  spaces and read `\` as `/`: `"/\t/evil.example"` would post off-site. QA
  applies the same check to the parsed HTML attribute. The engine has no backend: whoever
  hosts the bundle must answer that path (a `HUMAN-GATE` in the flow).
- `fields` is 1–4 unique names from `name`, `email`, `phone`, `message` and
  must contain `email` or `phone`. Each renders a visible `<label for>` and an
  input with the matching `type`, `autocomplete` and `required`.
- Rendered as `section.contact#contact` between FAQ and CTA. Inputs have a
  1 px `foreground` border (≥ 3:1 by W1) and a visible focus ring.
- QA: `form_action_unsafe` (absolute or scripted action), `form_label_missing`
  (an input without a label pointing to its id).
- The AI brief adapter cannot propose a form (the action is engine/host-owned).
- The Webflow guide builds it with `Form Block`, `Form settings ▸ Action / Method`.

## Recipe

Output bytes change, so `RECIPE_VERSION` becomes `landing-template-v4` and
`ENGINE_VERSION` `web-render.v4`. A key built under v3 replays as
`idempotency_key_reused_with_different_input`.

## Acceptance

- AC-W1 a theme with white on `#FACC15` primary is rejected with
  `contrast_hero_text_insufficient`; the default theme passes every pair.
- AC-W2 a `th` page's CSS has `line-height: 1.35` on the hero `h1`; CSS with a
  `line-height` below 1.3 on a `th` page fails QA; `en` pages keep `1.05`.
- AC-W3 a request with `contact_form` renders a labelled, `required` POST form
  to the relative action; an `https://` or `mailto:` action is rejected by
  `validate_request`; fields without `email`/`phone` are rejected; QA flags an
  unlabelled input and an absolute action.
- AC-W4 the Webflow guide lists the form steps and the new focus colours, and
  the reverse guide `docs/reverse/web-v1a/webflow.md` matches the engine (drift test).
