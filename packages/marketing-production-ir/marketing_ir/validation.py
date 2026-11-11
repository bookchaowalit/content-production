"""Pure validation and normalization for structured Marketing V1A requests."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from .models import AssetSpec, CampaignIR, CampaignRequest, ValidationIssue


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def _within_root(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_uri(uri: str, asset_root: str | None, path: str) -> ValidationIssue | None:
    lowered = uri.lower()
    if lowered.startswith(("http://", "https://", "javascript:", "data:", "file://")):
        return _issue("unsafe_asset_uri", path, "asset uri must be inline or a local path")
    if lowered.startswith("inline:"):
        return None
    if asset_root is None or not asset_root.strip():
        return _issue("asset_root_required", path, "asset_root is required for local asset paths")
    root = Path(asset_root).resolve()
    candidate = Path(uri)
    if not candidate.is_absolute():
        candidate = root / candidate
    if not _within_root(root, candidate.resolve(strict=False)):
        return _issue("asset_path_outside_root", path, "asset path must remain within asset_root")
    return None


def validate_request(request: Mapping[str, Any] | CampaignRequest) -> list[ValidationIssue]:
    """Return stable, actionable issues without mutating the supplied value."""

    if isinstance(request, CampaignRequest):
        parsed = request
    else:
        try:
            parsed = CampaignRequest.model_validate(request)
        except ValidationError as exc:
            issues: list[ValidationIssue] = []
            for error in exc.errors():
                path = ".".join(str(part) for part in error["loc"]) or "$"
                issues.append(_issue("invalid_request", path, str(error["msg"])))
            return issues

    issues = []
    if not parsed.campaign_id.strip():
        issues.append(_issue("campaign_id_required", "campaign_id", "campaign_id is required"))
    if not parsed.name.strip():
        issues.append(_issue("name_required", "name", "name is required"))

    seen_channels: set[str] = set()
    for index, channel in enumerate(parsed.channels):
        if channel in seen_channels:
            issues.append(_issue("duplicate_channel", f"channels.{index}", f"channel {channel!r} is repeated"))
        seen_channels.add(channel)

    seen_assets: set[str] = set()
    channels_with_assets: set[str] = set()
    for index, asset in enumerate(parsed.assets):
        asset_path = f"assets.{index}"
        if not asset.asset_id.strip():
            issues.append(_issue("asset_id_required", f"{asset_path}.asset_id", "asset_id is required"))
        if asset.asset_id in seen_assets:
            issues.append(_issue("duplicate_asset_id", f"{asset_path}.asset_id", f"asset_id {asset.asset_id!r} is repeated"))
        seen_assets.add(asset.asset_id)
        if asset.channel not in parsed.channels:
            issues.append(_issue("channel_not_requested", f"{asset_path}.channel", "asset channel must be listed in channels"))
        else:
            channels_with_assets.add(asset.channel)
        uri_issue = _validate_uri(asset.uri, parsed.asset_root, f"{asset_path}.uri")
        if uri_issue is not None:
            issues.append(uri_issue)
        if asset.text is not None and not asset.text.strip():
            issues.append(_issue("asset_text_required", f"{asset_path}.text", "asset text cannot be blank"))
        issues.extend(_email_field_issues(asset, asset_path))
        if asset.planned_at is not None and not _real_date(asset.planned_at):
            issues.append(_issue("invalid_planned_at", f"{asset_path}.planned_at", "planned_at must be a real date (YYYY-MM-DD[THH:MM])"))

    for channel in parsed.channels:
        if channel not in channels_with_assets:
            issues.append(_issue("channel_without_asset", f"channels.{channel}", "every channel needs at least one asset"))
    issues.extend(_variant_issues(parsed.assets))
    if parsed.utm is not None:
        issues.extend(_utm_issues(parsed.utm.landing_url))
    if parsed.timezone is not None and not _known_timezone(parsed.timezone):
        issues.append(_issue("unknown_timezone", "timezone", "timezone must be an IANA name such as Asia/Bangkok"))
    if parsed.brand_rules is not None and any(not word.strip() for word in parsed.brand_rules.forbidden_words):
        issues.append(_issue("forbidden_word_blank", "brand_rules.forbidden_words", "forbidden words cannot be blank"))
    return issues


EMAIL_FIELD_LIMITS = {"subject": 150, "preheader": 150}


def _email_field_issues(asset: AssetSpec, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for name, limit in EMAIL_FIELD_LIMITS.items():
        value = getattr(asset, name)
        if value is None:
            continue
        if asset.kind != "email":
            issues.append(_issue("email_field_not_email", f"{path}.{name}", "subject and preheader are only for email assets"))
        elif not value.strip():
            issues.append(_issue(f"{name}_required", f"{path}.{name}", f"{name} cannot be blank"))
        elif len(unicodedata.normalize("NFC", value)) > limit:
            issues.append(_issue(f"{name}_too_long", f"{path}.{name}", f"{name} exceeds {limit} characters"))
    return issues


def _real_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M" if "T" in value else "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _known_timezone(name: str) -> bool:
    if name.startswith(("/", ".")) or ".." in name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


MAX_VARIANTS = 4


def _variant_issues(assets: list[AssetSpec]) -> list[ValidationIssue]:
    """A/B variants: within one channel + kind, either no asset or every asset has a unique label (2-4)."""

    issues: list[ValidationIssue] = []
    groups: dict[tuple[str, str], list[AssetSpec]] = {}
    for asset in assets:
        groups.setdefault((asset.channel, asset.kind), []).append(asset)
    for (channel, kind), members in sorted(groups.items()):
        labels = [asset.variant for asset in members]
        if all(label is None for label in labels):
            continue
        path = f"assets.{channel}.{kind}"
        if any(label is None for label in labels):
            issues.append(_issue("variant_group_incomplete", path, "every asset of a channel and kind needs a variant once one has it"))
        elif len(set(labels)) != len(labels):
            issues.append(_issue("duplicate_variant", path, "variant labels must be unique within a channel and kind"))
        elif len(labels) < 2:
            issues.append(_issue("single_variant", path, "an A/B test needs at least two variants"))
        elif len(labels) > MAX_VARIANTS:
            issues.append(_issue("too_many_variants", path, "at most four variants per channel and kind"))
    return issues


def _utm_issues(url: str) -> list[ValidationIssue]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or any(character.isspace() for character in url):
        return [_issue("unsafe_landing_url", "utm.landing_url", "landing_url must be an https URL without spaces")]
    if any(key.lower().startswith("utm_") for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return [_issue("landing_url_has_utm", "utm.landing_url", "landing_url must not already carry utm_ parameters")]
    return []


def compile_campaign(request: Mapping[str, Any] | CampaignRequest) -> CampaignIR:
    """Validate and normalize a request into a deterministic, non-publishing IR."""

    parsed = request if isinstance(request, CampaignRequest) else CampaignRequest.model_validate(request)
    issues = validate_request(parsed)
    if issues:
        codes = ",".join(issue.code for issue in issues)
        raise ValueError(f"invalid_request:{codes}")
    assets = sorted(parsed.assets, key=lambda asset: (asset.asset_id, asset.channel, asset.kind))
    normalized_assets: list[AssetSpec] = []
    for asset in assets:
        if asset.uri.lower().startswith("inline:"):
            normalized_assets.append(asset)
            continue
        if parsed.asset_root is None:
            raise ValueError("invalid_request:asset_root_required")
        root = Path(parsed.asset_root).resolve()
        candidate = Path(asset.uri)
        if not candidate.is_absolute():
            normalized_uri = candidate.as_posix()
        else:
            normalized_uri = candidate.resolve(strict=False).relative_to(root).as_posix()
        normalized_assets.append(asset.model_copy(update={"uri": normalized_uri}))
    channels = sorted(parsed.channels)
    audience = parsed.audience.model_copy(
        update={"rules": sorted(parsed.audience.rules, key=lambda rule: (rule.field, rule.operator, rule.value))}
    )
    return CampaignIR(
        campaign_id=parsed.campaign_id,
        name=parsed.name,
        objective=parsed.objective,
        channels=channels,
        audience=audience,
        assets=normalized_assets,
        source_refs=[asset.uri for asset in normalized_assets],
        assumptions={"execution": "review_only", "publish": "disabled", "spend": "disabled"},
        utm=parsed.utm,
        timezone=parsed.timezone,
        brand_rules=parsed.brand_rules,
        experiment_plan=parsed.experiment_plan,
    )
