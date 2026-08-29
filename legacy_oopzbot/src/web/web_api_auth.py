"""Authentication helpers for the legacy Web player API."""

from __future__ import annotations

import secrets

READONLY_API_TOKEN_ENV = "OOPZ_READONLY_API_TOKEN"
READONLY_API_TOKEN_HEADER = "x-oopz-readonly-token"
READONLY_API_PATHS = frozenset({"/api/status", "/api/queue", "/api/lyric"})


def api_request_authorized(
    *,
    method: str,
    path: str,
    cookie_token: str,
    active_cookie_token: str,
    readonly_token: str,
    supplied_readonly_token: str,
) -> bool:
    """Accept the player cookie or a dedicated token on read-only endpoints."""

    if (
        active_cookie_token
        and cookie_token
        and secrets.compare_digest(cookie_token, active_cookie_token)
    ):
        return True

    return bool(
        method.upper() == "GET"
        and path in READONLY_API_PATHS
        and readonly_token
        and supplied_readonly_token
        and secrets.compare_digest(supplied_readonly_token, readonly_token)
    )
