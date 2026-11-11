"""Pure validation and compilation for structured Web V1A page requests."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .models import ContactFormSpec, LinkSpec, PageIR, PageRequest, ValidationIssue


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def _check_text(value: str, path: str, code: str) -> ValidationIssue | None:
    if not value.strip():
        return _issue(code, path, f"{path} is required")
    return None


def _check_href(link: LinkSpec, path: str) -> ValidationIssue | None:
    href = link.href.strip()
    lowered = href.lower()
    if lowered.startswith(("javascript:", "data:", "http://", "https:", "//")):
        return _issue("unsafe_href", f"{path}.href", "href must be a relative path, fragment, or mailto link")
    if not (href.startswith(("/", "#")) or lowered.startswith("mailto:")):
        return _issue("unsupported_href", f"{path}.href", "href must be a relative path, fragment, or mailto link")
    return None


# The exact string that is rendered must match: browsers drop tab/CR/LF and edge spaces from URLs and treat
# "\" like "/", so "/\t/evil.example" or "/\\evil.example" would post to another host if we normalised first.
FORM_ACTION_PATTERN = re.compile(r"/[A-Za-z0-9._~\-/]*")
_URL_IGNORED = re.compile(r"[\x00-\x20\x7f]")


def form_action_allowed(action: str) -> bool:
    """True only for a same-origin path: allowlisted characters, starts with one "/", never "//"."""

    return FORM_ACTION_PATTERN.fullmatch(action) is not None and "//" not in action


def _check_form(form: ContactFormSpec) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    action = form.action
    collapsed = _URL_IGNORED.sub("", action).replace("\\", "/").lower()
    if collapsed.startswith(("javascript:", "data:", "http:", "https:", "//")) or "//" in collapsed:
        issues.append(_issue("unsafe_form_action", "contact_form.action", "form action must be a relative path the host answers"))
    elif not form_action_allowed(action):
        issues.append(_issue("unsupported_form_action", "contact_form.action", "form action must be a plain relative path such as /api/lead"))
    if not {"email", "phone"} & set(form.fields):
        issues.append(_issue("form_contact_field_required", "contact_form.fields", "a lead form needs an email or phone field"))
    label = _check_text(form.submit_label, "contact_form.submit_label", "form_submit_label_required")
    if label is not None:
        issues.append(label)
    return issues


def validate_request(request: Mapping[str, Any] | PageRequest) -> list[ValidationIssue]:
    """Return stable, actionable issues without mutating the supplied value."""

    if isinstance(request, PageRequest):
        parsed = request
    else:
        try:
            parsed = PageRequest.model_validate(request)
        except ValidationError as exc:
            return [
                _issue("invalid_request", ".".join(str(part) for part in error["loc"]) or "$", str(error["msg"]))
                for error in exc.errors()
            ]

    issues: list[ValidationIssue] = []
    for field, code in (("page_id", "page_id_required"), ("slug", "slug_required"), ("title", "title_required"), ("description", "description_required"), ("hero_heading", "hero_heading_required"), ("hero_body", "hero_body_required")):
        issue = _check_text(getattr(parsed, field), field, code)
        if issue is not None:
            issues.append(issue)
    for path, link in (("hero_cta", parsed.hero_cta), ("secondary_cta", parsed.secondary_cta)):
        if link is not None:
            issue = _check_text(link.label, f"{path}.label", "cta_label_required")
            if issue is not None:
                issues.append(issue)
            href_issue = _check_href(link, path)
            if href_issue is not None:
                issues.append(href_issue)
    if not parsed.features:
        issues.append(_issue("features_required", "features", "at least one feature is required"))
    for index, feature in enumerate(parsed.features):
        title_issue = _check_text(feature.title, f"features.{index}.title", "feature_title_required")
        body_issue = _check_text(feature.body, f"features.{index}.body", "feature_body_required")
        if title_issue is not None:
            issues.append(title_issue)
        if body_issue is not None:
            issues.append(body_issue)
    for index, plan in enumerate(parsed.pricing):
        for path, value in ((f"pricing.{index}.name", plan.name), (f"pricing.{index}.price", plan.price)):
            issue = _check_text(value, path, "pricing_plan_text_required")
            if issue is not None:
                issues.append(issue)
        for item_index, item in enumerate(plan.features):
            issue = _check_text(item, f"pricing.{index}.features.{item_index}", "pricing_feature_text_required")
            if issue is not None:
                issues.append(issue)
    for index, faq in enumerate(parsed.faq):
        for path, value in ((f"faq.{index}.question", faq.question), (f"faq.{index}.answer", faq.answer)):
            issue = _check_text(value, path, "faq_text_required")
            if issue is not None:
                issues.append(issue)
    if parsed.contact_form is not None:
        issues.extend(_check_form(parsed.contact_form))
    return issues


def compile_page(request: Mapping[str, Any] | PageRequest) -> PageIR:
    """Validate and compile a fixed landing template IR."""

    parsed = request if isinstance(request, PageRequest) else PageRequest.model_validate(request)
    issues = validate_request(parsed)
    if issues:
        raise ValueError("invalid_request:" + ",".join(issue.code for issue in issues))
    return PageIR(
        page_id=parsed.page_id,
        language=parsed.language,
        slug=parsed.slug,
        title=parsed.title,
        description=parsed.description,
        hero_heading=parsed.hero_heading,
        hero_body=parsed.hero_body,
        hero_cta=parsed.hero_cta,
        features=parsed.features,
        pricing=parsed.pricing,
        faq=parsed.faq,
        contact_form=parsed.contact_form,
        secondary_cta=parsed.secondary_cta,
        footer_text=parsed.footer_text,
        theme=parsed.theme,
        hero_image=parsed.hero_image,
        source_refs=[parsed.slug],
    )
