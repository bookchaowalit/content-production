"""Parity pass v4 (spec docs/superpowers/specs/2026-11-12-web-parity.md): contrast QA, Thai line height, lead form."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from production_trace import render_manual_markdown
from web_ir import compile_page, validate_request
from web_production import jobs as jobs_module
from web_production.manual import build_webflow_guide
from web_production.qa import QA_CHECKS, contrast_ratio, contrast_report, run_page_qa
from web_production.render import THAI_HEADING_LINE_HEIGHT, render_css, render_html


def _page(**overrides: Any) -> dict[str, Any]:
    page: dict[str, Any] = {
        "page_id": "v4-01",
        "language": "th",
        "slug": "v4-page",
        "title": "ร้านขนมปังตัวอย่าง",
        "description": "ขนมปังสดใกล้บ้าน",
        "hero_heading": "ขนมปังอบใหม่ทุกเช้า",
        "hero_body": "อบสดทุกวันก่อนเจ็ดโมง",
        "hero_cta": {"label": "สั่งเลย", "href": "#signup"},
        "features": [{"title": "ซาวร์โดว์", "body": "หมักช้า"}],
    }
    page.update(overrides)
    return page


FORM = {"action": "/api/lead", "fields": ["name", "email", "message"], "submit_label": "ส่งข้อมูล"}


def _render(page: dict[str, Any]) -> tuple[str, str]:
    ir = compile_page(page)
    return render_html(ir, "styles.css"), render_css(ir)


# W1 contrast -------------------------------------------------------------


def test_contrast_ratio_matches_wcag_reference_values() -> None:
    assert contrast_ratio("#FFFFFF", "#000000") == 21.0
    assert round(contrast_ratio("#FFFFFF", "#2563EB"), 2) == 5.17
    assert round(contrast_ratio("#FBBF24", "#FFFFFF"), 2) == 1.67


def test_default_theme_passes_every_contrast_pair() -> None:
    _, css = _render(_page())
    report = contrast_report(css)
    assert {row["pair"] for row in report} == {"body_text", "hero_text", "button", "hero_button", "cta_text"}
    assert all(row["passes"] for row in report)
    assert "contrast" in QA_CHECKS


def test_low_contrast_theme_is_rejected_by_qa_and_job(tmp_path: Path) -> None:
    page = _page(theme={"primary": "#FACC15", "background": "#FFFFFF", "foreground": "#111827"})
    html, css = _render(page)
    assert "contrast_hero_text_insufficient" in run_page_qa(html, css, "styles.css")
    result = jobs_module.run_page_job(page, tmp_path / "out", "low-contrast")
    assert result.status == "rejected" and result.error_code == "contrast_hero_text_insufficient"
    assert not list((tmp_path / "out").glob("*.html"))


def test_focus_rings_use_contrast_checked_colours_not_yellow() -> None:
    _, css = _render(_page())
    assert "#FBBF24" not in css
    assert ".button:focus-visible { outline: .2rem solid var(--foreground);" in css
    assert ".hero .button:focus-visible { outline-color: #FFFFFF; }" in css


# W2 Thai line height ------------------------------------------------------


def test_thai_hero_heading_gets_thai_line_height_english_keeps_tight() -> None:
    _, th_css = _render(_page())
    _, en_css = _render(_page(language="en"))
    assert THAI_HEADING_LINE_HEIGHT == 1.35
    assert "line-height: 1.35;" in th_css and "line-height: 1.05;" not in th_css
    assert "line-height: 1.05;" in en_css


def test_qa_rejects_tight_line_height_on_thai_page_only() -> None:
    th_html, th_css = _render(_page())
    en_html, _ = _render(_page(language="en"))
    tight = th_css.replace("line-height: 1.35;", "line-height: 1.05;")
    assert "thai_line_height_too_tight" in run_page_qa(th_html, tight, "styles.css")
    assert "thai_line_height_too_tight" not in run_page_qa(en_html, tight, "styles.css")
    assert run_page_qa(th_html, th_css, "styles.css") == []


# W3 lead form -------------------------------------------------------------


def test_lead_form_renders_labelled_required_post_form() -> None:
    html, css = _render(_page(contact_form=FORM))
    assert '<section class="contact container" id="contact" aria-labelledby="contact-title">' in html
    assert '<form class="lead-form" action="/api/lead" method="post">' in html
    assert '<label for="lead-name">' in html and '<input id="lead-name" name="name" type="text" autocomplete="name" required>' in html
    assert '<input id="lead-email" name="email" type="email" autocomplete="email" required>' in html
    assert '<textarea id="lead-message" name="message" rows="4" required></textarea>' in html
    assert '<button class="button" type="submit">ส่งข้อมูล</button>' in html
    assert html.index('id="contact"') < html.index('id="signup"')
    assert ".lead-form input, .lead-form textarea {" in css
    assert run_page_qa(html, css, "styles.css") == []


def test_lead_form_request_validation() -> None:
    def codes(form: dict[str, Any]) -> list[str]:
        return [issue.code for issue in validate_request(_page(contact_form=form))]

    assert codes(FORM) == []
    assert "unsafe_form_action" in codes({**FORM, "action": "https://evil.example/collect"})
    assert "unsupported_form_action" in codes({**FORM, "action": "mailto:a@b.example"})
    assert "unsupported_form_action" in codes({**FORM, "action": "#contact"})
    assert "form_contact_field_required" in codes({**FORM, "fields": ["name", "message"]})
    assert "invalid_request" in codes({**FORM, "fields": ["name", "name", "email"]})
    assert "invalid_request" in codes({**FORM, "fields": ["email", "fax"]})


def test_qa_flags_unlabelled_input_and_absolute_action() -> None:
    html, css = _render(_page(contact_form=FORM))
    unlabelled = html.replace('<label for="lead-name">', '<label for="other">')
    assert "form_label_missing" in run_page_qa(unlabelled, css, "styles.css")
    absolute = html.replace('action="/api/lead"', 'action="https://evil.example/x"')
    assert "form_action_unsafe" in run_page_qa(absolute, css, "styles.css")
    for action in ("/&#9;/evil.example/collect", "/\t/evil.example/collect", "/\n/evil", "/\r/evil", " /contact", "/\\evil", "//x", "/a//b"):
        tricked = html.replace('action="/api/lead"', f'action="{action}"')
        assert "form_action_unsafe" in run_page_qa(tricked, css, "styles.css"), repr(action)
    plain = html.replace('action="/api/lead"', 'action="/contact"')
    assert run_page_qa(plain, css, "styles.css") == []


def test_webflow_guide_builds_the_form_and_new_focus_colours() -> None:
    ir = compile_page(_page(contact_form=FORM))
    text = render_manual_markdown(build_webflow_guide(ir, html_name="p.html", css_name="p.css"))
    assert "Add panel (A) ▸ Forms ▸ Form Block" in text
    assert "Form settings ▸ Action" in text and "/api/lead" in text and "POST" in text
    assert "#FBBF24" not in text
    assert "line-height 1.35" in text or "Height 1.35" in text


def test_recipe_is_v4() -> None:
    assert jobs_module.RECIPE_VERSION == "landing-template-v4"
    assert jobs_module.ENGINE_VERSION == "web-render.v4"
