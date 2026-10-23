"""Write-access gate for the API: fail closed unless a strong token is configured.

Reads stay open (the API is meant to sit behind a loopback port or a private network).
Every route that writes, stores media or queues work requires ``Authorization: Bearer
<VIDEO_EDITOR_API_TOKEN>``. A missing or short token refuses writes with 503
``auth_not_configured`` instead of silently allowing everyone. Local, loopback-only use
(the Compose stack) opts out explicitly with ``VIDEO_EDITOR_ALLOW_UNAUTHENTICATED=true``.
"""

from __future__ import annotations

import hmac

MIN_TOKEN_LENGTH = 24


def token_is_strong(token: str | None) -> bool:
    return token is not None and len(token) >= MIN_TOKEN_LENGTH


def token_matches(expected: str, presented: str | None) -> bool:
    """Constant-time comparison of the configured token with the presented one."""

    if not presented:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), presented.encode("utf-8"))
