"""Deterministic per-channel tools: copy and tag rules, UTM links, A/B groups and plans, calendar, bulk CSV.

These mirror what a person does in Meta Business Suite, LinkedIn, LINE OA
Manager, or an email tool before handing a post over for approval:

- **Hashtag and mention rules** (:func:`tag_checks`): Instagram refuses more
  than 30 hashtags or 20 mentions in one post (hard limit → the build is
  rejected); other findings are review flags (duplicate hashtags, hashtags on
  channels where they are not clickable, more than the recommended count).
- **UTM link builder** (:func:`tracked_links`): one tracked URL per copy,
  email, or landing-page asset, the same thing Google's Campaign URL Builder
  produces (``utm_source``/``utm_medium`` per channel, ``utm_campaign``,
  ``utm_content`` = asset id, optional ``utm_term``). Copy text is never
  rewritten; the link is a separate field to paste.
- **A/B groups** (:func:`experiments`): variants of one channel + kind.

Nothing here posts, schedules, sends, or spends.
"""

from __future__ import annotations

import csv
import io
import math
import re
import unicodedata
from statistics import NormalDist
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from marketing_ir import AssetSpec, CampaignIR

from .copy_rules import (
    CHANNEL_LIMITS,
    PLATFORM_LIMITS,
    claim_markers,
    copy_length,
    forbidden_hits,
    unsafe_markup,
)

# Post-ready frame sizes (px) to prepare in Canva for each channel's image slot.
FRAME_SIZES: dict[str, tuple[int, int, str]] = {
    "facebook": (1080, 1080, "Feed 1:1"),
    "instagram": (1080, 1350, "Feed 4:5"),
    "linkedin": (1200, 627, "Shared image 1.91:1"),
    "line": (1040, 1040, "Rich/Image message 1:1"),
    "email": (1200, 600, "Email header 2:1 (แสดงกว้าง 600 px)"),
    "web": (1200, 630, "Hero/OG image 1.91:1"),
}
SUBJECT_RECOMMENDED = 60
BULK_COLUMNS: tuple[str, ...] = (
    "campaign_id", "channel", "asset_id", "kind", "variant", "planned_at", "subject", "preheader",
    "text", "chars", "char_limit", "tracked_link", "frame_size", "source",
)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

HASHTAG = re.compile(r"(?<![\w&])#([^\s#@,.!?;:()\[\]{}\"'<>/\\|]+)")
MENTION = re.compile(r"(?<![\w.@])@([A-Za-z0-9_][A-Za-z0-9_.]{0,59})")
HARD_TAG_LIMITS: dict[str, dict[str, int]] = {"instagram": {"hashtags": 30, "mentions": 20}}
RECOMMENDED_HASHTAGS: dict[str, int] = {"facebook": 5, "instagram": 15, "linkedin": 5}
HASHTAGS_NOT_CLICKABLE = frozenset(("email", "line", "web"))
UTM_SOURCE_MEDIUM: dict[str, tuple[str, str]] = {
    "facebook": ("facebook", "social"),
    "instagram": ("instagram", "social"),
    "linkedin": ("linkedin", "social"),
    "email": ("newsletter", "email"),
    "line": ("line", "messaging"),
    "web": ("website", "referral"),
}
LINKED_KINDS = frozenset(("copy", "email", "landing_page"))


def hashtags_in(text: str) -> list[str]:
    return HASHTAG.findall(unicodedata.normalize("NFC", text))


def mentions_in(text: str) -> list[str]:
    return [mention.rstrip(".") for mention in MENTION.findall(text)]


def tag_checks(channel: str, text: str) -> list[dict[str, str]]:
    """Return ``{"code", "severity"}`` findings; ``severity`` is ``error`` or ``review``."""

    tags = hashtags_in(text)
    mentions = mentions_in(text)
    findings: list[dict[str, str]] = []
    limits = HARD_TAG_LIMITS.get(channel, {})
    if len(tags) > limits.get("hashtags", 10**6):
        findings.append({"code": "hashtag_limit_exceeded", "severity": "error"})
    if len(mentions) > limits.get("mentions", 10**6):
        findings.append({"code": "mention_limit_exceeded", "severity": "error"})
    if len({tag.casefold() for tag in tags}) != len(tags):
        findings.append({"code": "duplicate_hashtag", "severity": "review"})
    if tags and channel in HASHTAGS_NOT_CLICKABLE:
        findings.append({"code": "hashtags_not_clickable", "severity": "review"})
    recommended = RECOMMENDED_HASHTAGS.get(channel)
    if recommended is not None and len(tags) > recommended and not any(item["code"] == "hashtag_limit_exceeded" for item in findings):
        findings.append({"code": "hashtags_above_recommended", "severity": "review"})
    return findings


def copy_checks(channel: str, text: str, forbidden_words: list[str]) -> list[dict[str, str]]:
    """Copy rules on the structured build (spec M1); copy is never rewritten."""

    findings: list[dict[str, str]] = []
    length = copy_length(text)
    if length > PLATFORM_LIMITS.get(channel, 10**9):
        findings.append({"code": "copy_over_platform_limit", "severity": "error"})
    elif length > CHANNEL_LIMITS.get(channel, 10**9):
        findings.append({"code": "copy_over_engine_limit", "severity": "review"})
    if forbidden_hits(text, forbidden_words):
        findings.append({"code": "forbidden_word", "severity": "error"})
    if unsafe_markup(text):
        findings.append({"code": "unsafe_copy", "severity": "error"})
    if claim_markers(text):
        findings.append({"code": "claim_needs_evidence", "severity": "review"})
    return findings


def asset_checks(ir: CampaignIR) -> list[dict[str, str]]:
    forbidden = list(ir.brand_rules.forbidden_words) if ir.brand_rules is not None else []
    checks: list[dict[str, str]] = []
    for asset in ir.assets:
        findings: list[dict[str, str]] = []
        if asset.text:
            findings += copy_checks(asset.channel, asset.text, forbidden) + tag_checks(asset.channel, asset.text)
        for extra in (asset.subject, asset.preheader):
            if extra:
                findings += [item for item in copy_checks("email", extra, forbidden) if item["code"] != "copy_over_engine_limit"]
        if asset.subject and copy_length(asset.subject) > SUBJECT_RECOMMENDED:
            findings.append({"code": "subject_above_recommended", "severity": "review"})
        seen: set[str] = set()
        for finding in findings:
            if finding["code"] not in seen:
                seen.add(finding["code"])
                checks.append({"asset_id": asset.asset_id, "channel": asset.channel, **finding})
    return checks


def calendar(ir: CampaignIR) -> list[dict[str, str]]:
    """Planned dates as metadata (spec M3); nothing is scheduled."""

    rows = [{"planned_at": asset.planned_at, "channel": asset.channel, "asset_id": asset.asset_id} for asset in ir.assets if asset.planned_at]
    return sorted(rows, key=lambda row: (row["planned_at"], row["channel"], row["asset_id"]))


def ordered_assets(ir: CampaignIR) -> list[AssetSpec]:
    """Guide and bulk-sheet order: channel (as in the IR), then asset id."""

    return [asset for channel in ir.channels for asset in ir.assets if asset.channel == channel]


def _csv_cell(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_START) else value


def bulk_rows(ir: CampaignIR, links: list[dict[str, str]]) -> list[dict[str, str]]:
    """One row per asset for the bulk CSV (spec M4); cells are already formula-escaped."""

    link_by_asset = {item["asset_id"]: item["url"] for item in links}
    rows = []
    for asset in ordered_assets(ir):
        frame = FRAME_SIZES[asset.channel] if asset.kind in {"image", "video"} else None
        row = {
            "campaign_id": ir.campaign_id,
            "channel": asset.channel,
            "asset_id": asset.asset_id,
            "kind": asset.kind,
            "variant": asset.variant or "",
            "planned_at": asset.planned_at or "",
            "subject": asset.subject or "",
            "preheader": asset.preheader or "",
            "text": asset.text or "",
            "chars": str(copy_length(asset.text)) if asset.text else "",
            "char_limit": str(CHANNEL_LIMITS[asset.channel]) if asset.text else "",
            "tracked_link": link_by_asset.get(asset.asset_id, ""),
            "frame_size": f"{frame[0]}x{frame[1]}" if frame else "",
            "source": "" if asset.uri.lower().startswith("inline:") else asset.uri,
        }
        rows.append({key: _csv_cell(value) for key, value in row.items()})
    return rows


def bulk_csv(ir: CampaignIR, links: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(BULK_COLUMNS), lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(bulk_rows(ir, links))
    return buffer.getvalue().encode("utf-8")


def sample_size_per_variant(baseline_rate: float, lift: float, *, variants: int = 2, alpha: float = 0.05, power: float = 0.8) -> int:
    """Per-variant sample for a two-sided two-proportion z-test; Bonferroni over k-1 comparisons with A."""

    p1 = baseline_rate
    p2 = min(baseline_rate * (1 + lift), 0.999999)
    comparisons = max(variants - 1, 1)
    z_alpha = NormalDist().inv_cdf(1 - alpha / comparisons / 2)
    z_beta = NormalDist().inv_cdf(power)
    pooled = (p1 + p2) / 2
    numerator = z_alpha * math.sqrt(2 * pooled * (1 - pooled)) + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return math.ceil(numerator**2 / (p2 - p1) ** 2)


def build_tracked_url(landing_url: str, *, source: str, medium: str, campaign: str, content: str, term: str | None = None) -> str:
    """Append utm_* parameters (fixed order) after any existing query; keep the fragment."""

    parts = urlsplit(landing_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    utm = [("utm_source", source), ("utm_medium", medium), ("utm_campaign", campaign), ("utm_content", content)]
    if term:
        utm.append(("utm_term", term))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode([*query, *utm], quote_via=quote), parts.fragment))


def utm_campaign(ir: CampaignIR) -> str:
    if ir.utm is not None and ir.utm.campaign:
        return ir.utm.campaign
    return re.sub(r"[^a-z0-9._-]+", "-", ir.campaign_id.lower()).strip("-") or "campaign"


def tracked_links(ir: CampaignIR) -> list[dict[str, str]]:
    if ir.utm is None:
        return []
    links = []
    for asset in ir.assets:
        if asset.kind not in LINKED_KINDS:
            continue
        source, medium = UTM_SOURCE_MEDIUM[asset.channel]
        url = build_tracked_url(ir.utm.landing_url, source=source, medium=medium, campaign=utm_campaign(ir), content=asset.asset_id, term=ir.utm.term)
        links.append({"asset_id": asset.asset_id, "channel": asset.channel, "utm_source": source, "utm_medium": medium, "url": url})
    return links


def experiments(ir: CampaignIR) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for asset in ir.assets:
        if asset.variant is not None:
            groups.setdefault((asset.channel, asset.kind), []).append({"variant": asset.variant, "asset_id": asset.asset_id})
    result: list[dict[str, Any]] = []
    for (channel, kind), members in sorted(groups.items()):
        group: dict[str, Any] = {"channel": channel, "kind": kind, "variants": sorted(members, key=lambda item: item["variant"])}
        plan = ir.experiment_plan
        if plan is not None:
            sample = sample_size_per_variant(plan.baseline_rate, plan.minimum_detectable_lift, variants=len(members), alpha=plan.alpha, power=plan.power)
            group["plan"] = {
                "metric": plan.metric,
                "baseline_rate": plan.baseline_rate,
                "minimum_detectable_lift": plan.minimum_detectable_lift,
                "alpha": plan.alpha,
                "power": plan.power,
                "sample_per_variant": sample,
                "duration_days": math.ceil(sample / plan.daily_reach_per_variant),
            }
        result.append(group)
    return result


__all__ = [
    "BULK_COLUMNS",
    "FRAME_SIZES",
    "HARD_TAG_LIMITS",
    "RECOMMENDED_HASHTAGS",
    "UTM_SOURCE_MEDIUM",
    "asset_checks",
    "build_tracked_url",
    "bulk_csv",
    "bulk_rows",
    "calendar",
    "copy_checks",
    "experiments",
    "hashtags_in",
    "mentions_in",
    "ordered_assets",
    "sample_size_per_variant",
    "tag_checks",
    "tracked_links",
    "utm_campaign",
]
