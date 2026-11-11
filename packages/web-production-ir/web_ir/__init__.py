"""Versioned contracts for the deterministic Web V1A product."""

from .models import (
    ArtifactRef,
    ContactFormSpec,
    FaqItem,
    FeatureSpec,
    HeroImageSpec,
    LinkSpec,
    PageIR,
    PageRequest,
    PricingPlan,
    ThemeSpec,
    ValidationIssue,
)
from .validation import (
    FORM_ACTION_PATTERN,
    compile_page,
    form_action_allowed,
    validate_request,
)

__all__ = [
    "FORM_ACTION_PATTERN",
    "ArtifactRef",
    "ContactFormSpec",
    "FaqItem",
    "FeatureSpec",
    "HeroImageSpec",
    "LinkSpec",
    "PageIR",
    "PageRequest",
    "PricingPlan",
    "ThemeSpec",
    "ValidationIssue",
    "compile_page",
    "form_action_allowed",
    "validate_request",
]
