---
title: Marketing V1A parity pass (copy rules, email fields, calendar, bulk CSV, approval, A/B plan)
date: 2026-11-12
status: accepted (owner goal 2026-11-12, content-production parity)
tool: marketing-v1a
---

# Marketing V1A parity pass

Benchmarks: Canva (campaign / brand kit / Magic Write), HubSpot campaigns,
Meta Ads Manager / Business Suite (placement copy limits, UTM, A/B test), Google
Ads RSA. Matrix: `docs/parity/marketing.md`. Recipe moves to
`campaign-package-v3`; v1 and v2 manifests still replay.

Everything stays review-only: nothing publishes, schedules, sends, targets, or
spends. All parts below are `LOGIC` except the approval record (`HUMAN-GATE`).

## M1 Copy rules on the structured build

`channel_tools.asset_checks` runs, for every asset with text:

- platform hard limits (`PLATFORM_LIMITS`: Instagram caption 2,200, LinkedIn
  post 3,000, Facebook post 63,206, LINE text message 5,000) → `error`
  `copy_over_platform_limit` (build rejected);
- engine limit (`CHANNEL_LIMITS`, 300–1,200) → `review` `copy_over_engine_limit`;
- forbidden words (baseline list + optional `brand_rules.forbidden_words` on the
  request) → `error` `forbidden_word`. ASCII words match on word boundaries
  (`cure` does not match `secure`); Thai words match as substrings;
- markup / control characters → `error` `unsafe_copy`;
- claim markers (best, #1, award, guarantee, ที่สุด, อันดับ 1 …) → `review`
  `claim_needs_evidence` (no brief to ground against on this path).

Copy is never rewritten.

## M2 Email subject and preheader

`AssetSpec.subject` / `AssetSpec.preheader` (only on `kind: email`, otherwise
`email_field_not_email`). Subject ≤ 150 and preheader ≤ 150 characters
(`error` `subject_too_long` / `preheader_too_long`); subject > 60 → `review`
`subject_above_recommended`. Blank values are rejected.

## M3 Planned dates (content calendar, metadata only)

`AssetSpec.planned_at` = `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` (a real date),
optional `CampaignRequest.timezone` (IANA name, checked with `zoneinfo`). The
package gets `calendar`: one row per dated asset sorted by date, channel, asset.
Nothing is scheduled; the manual says "วันที่วางแผน" only.

## M4 Bulk CSV export

Every accepted job writes `<campaign>-<hash>.bulk.csv` next to the package
(UTF-8, RFC 4180, `\r\n`, header row), one row per asset in guide order
(channel, then asset id). Columns (`BULK_COLUMNS`):
`campaign_id, channel, asset_id, kind, variant, planned_at, subject, preheader,
text, chars, char_limit, tracked_link, frame_size, source`. Cells starting with
`= + - @`, tab or CR are prefixed with `'` (CSV formula injection). The manifest
lists it under `exports` and the trace lists it as an artifact. It is a generic
interchange sheet; mapping to one platform's own import template is P2.

## M5 Approval gate

`marketing_production.approval.record_approval(package_path, decision, reviewer,
note, decided_at)` writes `<package stem>.approval.json`
(`campaign-approval.v1`: package filename + sha256, decision
`approved | changes_requested | rejected`, reviewer label, note). `approval_status`
returns `pending_review` without a record, the decision when the hash matches,
and `stale` when the package changed. CLI: `approve` and `approval-status`.
Approval never publishes anything.

## M6 A/B experiment plan

Optional `CampaignRequest.experiment_plan` (`metric`, `baseline_rate` in (0,1),
`minimum_detectable_lift` relative > 0, `daily_reach_per_variant` ≥ 1, `alpha`
0.05, `power` 0.8). For each experiment group with k variants the package
`experiments[*].plan` holds the per-variant sample size (two-sided two-proportion
z-test, Bonferroni alpha/(k-1) against variant A) and `duration_days` =
ceil(n / daily reach). Reference: baseline 10 %, lift 20 %, k = 2 → 3,841 per
variant.

## Acceptance

Each M has failing tests first in `services/marketing-production-api/tests/test_parity_pass.py`
(and IR tests for new fields). A request without the new fields keeps its
package bytes (no new keys).
