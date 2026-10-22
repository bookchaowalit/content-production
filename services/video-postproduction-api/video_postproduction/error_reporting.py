"""Opt-in Sentry error reporting for the API (errors only, no traces, no PII).

Off unless ``SENTRY_DSN`` is set and the ``sentry`` extra is installed
(``pip install .[sentry]``). Events pass through :func:`scrub_event`, an allowlist: only the
exception type, the stack frames' file/function/line, the request method and path, and a few
fixed fields survive. Exception messages, local variables, headers, cookies, query strings,
bodies, user data and breadcrumbs are dropped, so uploaded file names, transcripts and tokens
never leave the host.
"""

from __future__ import annotations

import os
from importlib import import_module
from typing import Any
from urllib.parse import urlsplit

_TOP_LEVEL_KEYS = frozenset(
    {"event_id", "timestamp", "level", "platform", "logger", "environment", "release", "transaction", "tags"}
)
_FRAME_KEYS = ("filename", "module", "function", "lineno", "in_app")


def _scrub_exception(exception: dict[str, Any]) -> dict[str, Any]:
    values = []
    for value in exception.get("values") or []:
        frames = ((value.get("stacktrace") or {}).get("frames")) or []
        values.append(
            {
                "type": value.get("type"),
                "module": value.get("module"),
                "stacktrace": {"frames": [{k: f[k] for k in _FRAME_KEYS if k in f} for f in frames]},
            }
        )
    return {"values": values}


def scrub_event(event: dict[str, Any], hint: object = None) -> dict[str, Any]:
    """Allowlist ``before_send``: keep only fields that cannot carry user content."""

    clean: dict[str, Any] = {k: v for k, v in event.items() if k in _TOP_LEVEL_KEYS}
    if "exception" in event:
        clean["exception"] = _scrub_exception(event["exception"])
    request = event.get("request")
    if isinstance(request, dict):
        url = request.get("url")
        clean["request"] = {
            "method": request.get("method"),
            "url": urlsplit(url).path if isinstance(url, str) else None,
        }
    return clean


def init_error_reporting() -> bool:
    """Initialise Sentry when configured; return whether it is on."""

    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        sdk = import_module("sentry_sdk")
    except ImportError:
        return False
    sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "local"),
        send_default_pii=False,
        traces_sample_rate=0.0,
        max_breadcrumbs=0,
        before_send=scrub_event,
    )
    return True
