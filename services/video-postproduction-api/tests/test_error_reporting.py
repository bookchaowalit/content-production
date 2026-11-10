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


def test_scrub_event_drops_logentry_log_text():
    event = {
        "event_id": "abc",
        "level": "error",
        "logger": "httpx",
        "logentry": {"message": "GET https://api.test/v1?token=leaky-token failed"},
    }
    clean = scrub_event(event)
    assert "logentry" not in clean
    assert "leaky-token" not in repr(clean)


def test_scrub_event_drops_message_not_from_our_reporters():
    # The service never calls capture_message, so any message is free log text.
    clean = scrub_event({"event_id": "abc", "message": "transcript for somchai@example.test"})
    assert "message" not in clean
    assert "somchai" not in repr(clean)


def test_scrub_event_drops_exception_value():
    clean = scrub_event({"exception": {"values": [{"type": "KeyError", "value": "'token=abc123'"}]}})
    value = clean["exception"]["values"][0]
    assert value["type"] == "KeyError"
    assert "value" not in value


class _FakeLoggingIntegration:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _install_fake_sdk(monkeypatch, calls):
    fake = types.ModuleType("sentry_sdk")
    fake.init = lambda **kwargs: calls.append(kwargs)  # type: ignore[attr-defined]
    logging_mod = types.ModuleType("sentry_sdk.integrations.logging")
    logging_mod.LoggingIntegration = _FakeLoggingIntegration  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.logging", logging_mod)
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")


def test_error_reporting_disables_log_to_event_and_breadcrumbs(monkeypatch):
    calls: list[dict[str, object]] = []
    _install_fake_sdk(monkeypatch, calls)

    assert init_error_reporting() is True
    integrations = calls[0]["integrations"]
    assert isinstance(integrations, list) and len(integrations) == 1
    assert isinstance(integrations[0], _FakeLoggingIntegration)
    assert integrations[0].kwargs == {"level": None, "event_level": None, "sentry_logs_level": None}


def test_error_reporting_is_off_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_error_reporting() is False


def test_error_reporting_initialises_with_the_scrubber(monkeypatch):
    calls: list[dict[str, object]] = []
    _install_fake_sdk(monkeypatch, calls)

    assert init_error_reporting() is True
    assert calls[0]["before_send"] is scrub_event
    assert calls[0]["send_default_pii"] is False
    assert calls[0]["traces_sample_rate"] == 0.0


def test_report_exception_is_a_no_op_without_sentry(monkeypatch):
    import sys

    from video_postproduction.error_reporting import report_exception

    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # import fails
    report_exception(RuntimeError("x"))  # must not raise


def test_report_exception_sends_to_sentry(monkeypatch):
    import sys
    import types

    from video_postproduction.error_reporting import report_exception

    sent = []
    fake = types.SimpleNamespace(capture_exception=sent.append)
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    err = RuntimeError("x")
    report_exception(err)
    assert sent == [err]
