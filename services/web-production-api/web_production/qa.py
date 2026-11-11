"""Deterministic build, accessibility, SEO, and responsive checks for Web V1A artifacts."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .render import BREAKPOINTS, ON_PRIMARY

# WCAG 2.2 AA (1.4.3) minimum for normal-size text; every pair below paints body-size text.
AA_TEXT = 4.5
# Lowest line-height allowed on a ``lang="th"`` page (stacked vowels and tone marks).
THAI_MIN_LINE_HEIGHT = 1.3
_THEME_VAR = re.compile(r"--(primary|background|foreground):\s*(#[0-9A-Fa-f]{6})\s*;")
_LINE_HEIGHT = re.compile(r"line-height:\s*([0-9]*\.?[0-9]+)\s*;")


def _luminance(color: str) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio of two ``#RRGGBB`` colours (1.0–21.0)."""

    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return round((lighter + 0.05) / (darker + 0.05), 4)


def _mix(first: str, weight: float, second: str) -> str:
    channels = [round(int(first[i : i + 2], 16) * weight + int(second[i : i + 2], 16) * (1 - weight)) for i in (1, 3, 5)]
    return "#" + "".join(f"{value:02X}" for value in channels)


def contrast_report(css_text: str) -> list[dict[str, object]]:
    """Every text / focus colour pair the template paints, from the theme variables in the engine CSS.

    Focus rings reuse these colours (``foreground`` on the page and CTA, white in the hero), so a
    passing text pair also gives the ring ≥ 3:1 against its background (WCAG 1.4.11).
    """

    theme = {name: value.upper() for name, value in _THEME_VAR.findall(css_text)}
    if set(theme) != {"primary", "background", "foreground"}:
        return [{"pair": "theme", "foreground": "", "background": "", "ratio": 0.0, "minimum": AA_TEXT, "passes": False}]
    primary, background, foreground = theme["primary"], theme["background"], theme["foreground"]
    pairs = (
        ("body_text", foreground, background),
        ("hero_text", ON_PRIMARY, primary),
        ("button", ON_PRIMARY, foreground),
        ("hero_button", foreground, ON_PRIMARY),
        ("cta_text", foreground, _mix(primary, 0.12, background)),
    )
    report: list[dict[str, object]] = []
    for name, fore, back in pairs:
        ratio = contrast_ratio(fore, back)
        report.append({"pair": name, "foreground": fore, "background": back, "ratio": round(ratio, 2), "minimum": AA_TEXT, "passes": ratio >= AA_TEXT})
    return report

# Meta tags every page must carry (name/property → issue code when missing or empty).
REQUIRED_META: tuple[tuple[str, str, str], ...] = (
    ("name", "description", "meta_description_missing"),
    ("property", "og:type", "og_type_missing"),
    ("property", "og:title", "og_title_missing"),
    ("property", "og:description", "og_description_missing"),
    ("property", "og:locale", "og_locale_missing"),
    ("name", "twitter:card", "twitter_card_missing"),
)


class _MarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attrs: list[tuple[str, dict[str, str]]] = []
        self.title_text = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs.append((tag, {key: value or "" for key, value in attrs}))
        self._in_title = tag == "title"

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_text += data


# Names of the check groups ``run_page_qa`` applies (recorded in the job trace).
QA_CHECKS: tuple[str, ...] = (
    "doctype", "no_script", "viewport", "html_parse", "required_landmarks", "single_h1", "bundle_local_src",
    "image_alt", "safe_href", "stylesheet_reference", "title", "meta_description", "open_graph", "twitter_card",
    "focus_visible", *(f"breakpoint_{name}" for name, _ in BREAKPOINTS), "no_external_css",
    "contrast", "thai_line_height", "form_action", "form_labels",
)


def run_page_qa(
    html_text: str,
    css_text: str,
    expected_css_filename: str | None = None,
    bundle_assets: frozenset[str] = frozenset(),
) -> list[str]:
    """Return stable QA issue codes without executing page content.

    ``src`` is allowed only for files shipped in the same bundle
    (``bundle_assets``); every image must carry a non-empty ``alt``. The head
    must carry a non-empty ``<title>``, meta description, and OpenGraph /
    Twitter card meta, and the CSS must define every named breakpoint.
    """

    issues: list[str] = []
    lowered = html_text.lower()
    if not lowered.startswith("<!doctype html>"):
        issues.append("doctype_missing")
    if "<script" in lowered:
        issues.append("unsafe_markup")
    if '<meta name="viewport"' not in lowered:
        issues.append("viewport_missing")
    parser = _MarkupParser()
    try:
        parser.feed(html_text)
    except (TypeError, ValueError):
        issues.append("html_parse_failed")
    for required in ("html", "head", "main", "footer", "h1"):
        if required not in parser.tags:
            issues.append(f"{required}_missing")
    if parser.tags.count("h1") != 1:
        issues.append("h1_count_invalid")
    for tag, attrs in parser.attrs:
        href = attrs.get("href")
        src = attrs.get("src")
        if src and src not in bundle_assets:
            issues.append("external_asset_reference")
        if tag == "img" and not (attrs.get("alt") or "").strip():
            issues.append("image_alt_missing")
        if href and (href.lower().startswith(("http://", "https://", "javascript:", "data:", "//"))):
            issues.append("unsafe_href")
        if expected_css_filename and tag == "link" and href != expected_css_filename:
            issues.append("stylesheet_reference_invalid")
    if not parser.title_text.strip():
        issues.append("title_missing")
    metas = [attrs for tag, attrs in parser.attrs if tag == "meta"]
    for attribute, name, code in REQUIRED_META:
        if not any(meta.get(attribute) == name and meta.get("content", "").strip() for meta in metas):
            issues.append(code)
    if "@media" not in css_text or ":focus-visible" not in css_text:
        issues.append("responsive_or_focus_css_missing")
    for name, width in BREAKPOINTS:
        if not re.search(rf"@media\s*\(\s*max-width:\s*{width}px\s*\)", css_text):
            issues.append(f"breakpoint_{name}_missing")
    if "url(" in css_text.lower() or "@import" in css_text.lower():
        issues.append("external_css_reference")
    issues += [f"contrast_{row['pair']}_insufficient" for row in contrast_report(css_text) if not row["passes"]]
    language = next((attrs.get("lang", "") for tag, attrs in parser.attrs if tag == "html"), "")
    if language == "th" and any(float(value) < THAI_MIN_LINE_HEIGHT for value in _LINE_HEIGHT.findall(css_text)):
        issues.append("thai_line_height_too_tight")
    issues += _form_issues(parser.attrs)
    return sorted(set(issues))


def _form_issues(attrs: list[tuple[str, dict[str, str]]]) -> list[str]:
    """Forms post only to a relative path, and every control has a visible ``<label for>``."""

    issues: list[str] = []
    for tag, values in attrs:
        if tag == "form":
            action = values.get("action", "")
            if not action.startswith("/") or action.startswith("//") or values.get("method", "").lower() != "post":
                issues.append("form_action_unsafe")
    labelled = {values.get("for", "") for tag, values in attrs if tag == "label"}
    for tag, values in attrs:
        if tag in ("input", "textarea", "select") and values.get("id", "") not in labelled:
            issues.append("form_label_missing")
    return issues
