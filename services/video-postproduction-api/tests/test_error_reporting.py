from __future__ import annotations

import sys
import types

from video_postproduction.error_reporting import init_error_reporting, scrub_event


def test_scrub_event_keeps_only_allowlisted_fields():
    event = {
        "event_id": "abc",
        "level": "error",
        "environment": "local",
        "message": "upload interview-with-somchai.mp4 failed",
        "user": {"ip_address": "203.0.113.9"},
        "breadcrumbs": {"values": [{"message": "secret transcript"}]},
        "extra": {"token": "should-not-leave"},
        "request": {
            "method": "POST",
            "url": "http://host/api/v1/projects?token=abc",
            "headers": {"Authorization": "Bearer secret-token"},
            "cookies": "session=1",
            "data": {"name": "client project"},
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad file /home/user/interview.mp4",
                    "stacktrace": {
                        "frames": [
                            {"filename": "routes.py", "function": "upload", "lineno": 3, "vars": {"token": "x"}}
                        ]
                    },
                }
            ]
        },
    }

    clean = scrub_event(event)

    assert clean["request"] == {"method": "POST", "url": "/api/v1/projects"}
    assert clean["exception"]["values"][0]["type"] == "ValueError"
    assert "value" not in clean["exception"]["values"][0]
    assert clean["exception"]["values"][0]["stacktrace"]["frames"] == [
        {"filename": "routes.py", "function": "upload", "lineno": 3}
    ]
    for dropped in ("message", "user", "breadcrumbs", "extra"):
        assert dropped not in clean
    flattened = repr(clean)
    for secret in ("secret-token", "somchai", "203.0.113.9", "client project", "token=abc", "interview"):
        assert secret not in flattened


def test_error_reporting_is_off_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_error_reporting() is False


def test_error_reporting_initialises_with_the_scrubber(monkeypatch):
    calls: list[dict[str, object]] = []
    fake = types.ModuleType("sentry_sdk")
    fake.init = lambda **kwargs: calls.append(kwargs)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")

    assert init_error_reporting() is True
    assert calls[0]["before_send"] is scrub_event
    assert calls[0]["send_default_pii"] is False
    assert calls[0]["traces_sample_rate"] == 0.0
