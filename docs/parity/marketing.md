# Marketing V1A — feature parity with Canva, HubSpot, Meta Ads Manager / Business Suite, and Google Ads

Scope: `services/marketing-production-api` (`marketing_production`) + `packages/marketing-production-ir`
(tool id `marketing-v1a`, recipe `campaign-package-v3`). The engine turns a structured
campaign request into a **review-only** package: per-channel copy and asset slots,
checks, tracked links, A/B groups with a test plan, a planned-date calendar, and a bulk
CSV. It never publishes, schedules, sends, targets an audience, or spends — so the
platforms' delivery features are `—` by design, not gaps. Channels: Facebook, Instagram,
LinkedIn, email, LINE, web (organic posts, email, messaging, site copy). Paid ad formats
are not in V1A yet (see the P2 rows).

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope for a deterministic, review-only batch engine.

## Capability matrix

| Capability | Canva / HubSpot / Meta Ads Manager / Google Ads | Engine status | Priority | Notes | Flow |
|---|---|---|---|---|---|
| One campaign, several channels | HubSpot campaigns; Meta multi-placement; Canva campaign folders | have | — | facebook, instagram, linkedin, email, line, web in one package | LOGIC |
| Per-channel copy and asset slots | Meta "Primary text / Media"; HubSpot social + email | have | — | `assets[]` per channel and kind (copy, image, video, email, landing_page) | LOGIC |
| Copy limits per channel, checked on every build | Meta / IG / LinkedIn counters; Google RSA 30/90 | **have (new)** | — | platform hard limits reject (`copy_over_platform_limit`: IG 2,200, LinkedIn 3,000, FB 63,206, LINE 5,000); engine limits 300–1,200 flag `copy_over_engine_limit` for review. Copy is never cut | LOGIC |
| Brand voice: forbidden words | Canva Brand Voice; HubSpot brand kit | **have (new)** | — | baseline list + `brand_rules.forbidden_words` reject the build (`forbidden_word`); Latin words match whole words (`cure` ≠ `secure`), Thai as substrings | LOGIC |
| Unsupported claims (best, #1, award, guarantee, ที่สุด, อันดับ 1 …) | HubSpot / Hootsuite compliance review | **have (new)** | — | structured build flags `claim_needs_evidence` for review; the AI path requires a verbatim brief quote for every claim | LOGIC |
| Markup / control characters in copy | — | **have (new)** | — | `unsafe_copy` rejects | LOGIC |
| Hashtag and mention rules | IG 30 hashtags / 20 mentions | have | — | hard IG limits reject; duplicates, non-clickable, above recommended flagged | LOGIC |
| Thai text | all (Unicode) | have | — | lengths counted after NFC; Thai digits normalised for grounding; Thai forbidden words and claim markers | LOGIC |
| UTM builder | Google Campaign URL Builder; Meta URL parameters; HubSpot tracking URLs | have | — | `utm_source/medium/campaign/content(/term)` per asset, fixed order, existing query and fragment kept | LOGIC |
| Link validation | Meta link check | partial | P2 | landing URL must be `https`, no spaces, no existing `utm_`; no reachability / OG preview check (needs network) | LOGIC |
| Email subject and preview text | HubSpot / Mailchimp email settings | **have (new)** | — | `subject` / `preheader` on email assets; ≤ 150 characters each, subject > 60 flagged | LOGIC |
| A/B variants | Meta A/B test; HubSpot A/B email; Google RSA asset combinations | have | — | 2–4 labelled variants per channel + kind, listed under `experiments` | LOGIC |
| A/B test plan: sample size and duration | Meta A/B test power estimate; HubSpot test sample % | **have (new)** | — | `experiment_plan` → per-variant sample (two-sided two-proportion z-test, Bonferroni vs A) and days at the given daily reach; no live test, no winner pick | LOGIC |
| Planned dates / content calendar | HubSpot calendar; Meta Planner | **have (new)** | — | `planned_at` per asset + campaign `timezone`; package `calendar`, metadata only (never scheduled) | LOGIC |
| Bulk CSV export | Meta bulk upload; Google Ads Editor import; Hootsuite Bulk Composer | **have (new)** | — | `<campaign>-<hash>.bulk.csv` (UTF-8, RFC 4180), one row per asset, 14 columns, formula-escaped; listed in `manifest.exports` | LOGIC |
| Platform-specific import templates | Meta / Google Ads Editor / Hootsuite column sets | missing | P2 | the CSV is generic; mapping needs each platform's current template checked in a real account | LOGIC |
| Asset spec per placement (frame size) | Canva presets; Meta crop guides | partial | P2 | frame size per channel in the CSV and the guide; no safe zones, no resize — hand-off to `graphics-v1a` | LOGIC |
| Paid ad formats (Meta ad: primary text / headline / description; Google RSA 15 × 30 + 4 × 90, paths 15) | Meta Ads Manager; Google Ads | missing | P2 | new asset kinds with their own limits; review-only like the rest | LOGIC |
| Approval gate | HubSpot / Hootsuite approvals | **have (new)** | — | `approve` / `approval-status`: decision bound to the package sha256; a changed package is `stale` | HUMAN-GATE |
| Save as draft for review | Meta / HubSpot drafts | have | — | the package is the draft; the guide ends every channel with "Save as draft" | LOGIC |
| Copy from a brief | Canva Magic Write; HubSpot AI | have (shadow; live by owner approval) | — | `propose` through `brief-adapter-kit` → AI gateway; grounding rejects ungrounded numbers, links, claims | AI-ASSIST |
| Brand kit, templates, visual design | Canva | — | — | graphics belong to `graphics-v1a` | — |
| More channels (X, TikTok, YouTube, Threads) | HubSpot / Meta / Hootsuite | missing | P2 | | LOGIC |
| Link shortener | Bitly / Ow.ly | — | — | external service | — |
| Publish, schedule, send, boost, audience targeting, budget | all | — | — | excluded by design (review-only V1A) | — |
| Analytics, reporting, inbox | all | — | — | needs platform APIs | — |
| Source lineage and replay-safe packages | — | have | — | asset hashes, idempotent replay of v1/v2/v3 manifests | LOGIC |
| Job history, trace, manual guide | — | have | — | `jobs/<key>/trace.json`, `MANUAL-STEPS.md`, `history.jsonl` | LOGIC |
| Make it by hand (reverse guide) | — | **have (new)** | — | [docs/reverse/marketing-v1a/meta-business-suite.md](../reverse/marketing-v1a/meta-business-suite.md); Calc section verified, web UIs pending owner ([VERIFY.md](../reverse/marketing-v1a/VERIFY.md)) | LOGIC |

Flow column: `LOGIC` rules/code (repeatable), `AI-ASSIST` a model proposes through the AI gateway and a `LOGIC` step validates it, `HUMAN-GATE` a person decides, `—` out of scope. See [the flow spec](../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md) and `marketing_production/flow.py`.

## This pass (2026-11-12)

Spec: [2026-11-12-marketing-parity.md](../superpowers/specs/2026-11-12-marketing-parity.md).
Before: 8 have, 3 partial, 9 missing, 5 open P1 (structured copy rules, approval,
bulk CSV, email subject/preheader, calendar). After: no open P1.

1. M1 copy rules on the structured build — `test_m1_*` in `tests/test_parity_pass.py`.
2. M2 email subject / preheader — `test_m2_email_fields`, IR `test_v3_field_issues`.
3. M3 planned dates and calendar — `test_m3_calendar_is_sorted_metadata`.
4. M4 bulk CSV — `test_m4_bulk_csv_rows_and_formula_escape`, `test_m4_image_row_has_frame_size`.
5. M5 approval gate — `test_m5_approval_record_is_bound_to_package_hash`, `test_m5_cli_approve_and_status`.
6. M6 A/B plan — `test_m6_sample_size_matches_reference` (10 % → +20 % = 3,841 per variant), `test_m6_experiment_plan_in_package`.
7. Reverse guide + drift test `tests/test_reverse_guide.py`; v2 manifests still replay (`test_replay_of_v2_manifest_still_works_after_v3`).

## Roadmap

- P2 — paid ad formats (Meta ad fields, Google RSA) with their limits; platform import
  templates for the bulk CSV; safe zones / resize through `graphics-v1a`; link reachability
  and preview checks; more channels.
