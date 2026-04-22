"""Shared auth helpers used by every source router.

Auth model
----------
* If ``API_TOKEN`` is set, every routed endpoint requires
  ``Authorization: Bearer <API_TOKEN>``. Comparison is constant-time;
  missing-vs-wrong header returns the same 401 with the same body.
* If ``API_TOKEN`` is unset:
  * ``API_REQUIRE_TOKEN=true``  → ``ensure_auth_configured()`` raises at
    startup so the app refuses to boot misconfigured. Set this in any
    public deployment (Render, Fly, etc).
  * ``API_REQUIRE_TOKEN=false`` (default) → auth is a no-op and a loud
    WARNING is logged. Convenient for local dev only.
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException

log = logging.getLogger(__name__)

API_TOKEN: Optional[str] = os.getenv("API_TOKEN") or None
REQUIRE_TOKEN: bool = os.getenv("API_REQUIRE_TOKEN", "false").lower() in {"1", "true", "yes"}

_BEARER_PREFIX = "Bearer "


class AuthMisconfigured(RuntimeError):
    """Raised at startup when REQUIRE_TOKEN is on but no token is set."""


def ensure_auth_configured() -> None:
    """Hard-fail at import / startup if a public deployment forgot the token.

    Called from ``app.py`` at module load time so misconfiguration shows up
    in the build logs, not on the first inbound request.
    """
    if REQUIRE_TOKEN and not API_TOKEN:
        raise AuthMisconfigured(
            "API_REQUIRE_TOKEN=true but API_TOKEN is empty. "
            "Set API_TOKEN to a strong random secret (e.g. `openssl rand -hex 16`)."
        )
    if not API_TOKEN:
        log.warning(
            "API_TOKEN is unset and API_REQUIRE_TOKEN is false — every route "
            "is publicly reachable. Only acceptable on a private network."
        )


def bearer_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency: reject requests with a missing / wrong bearer token.

    Returns the **same** 401 body for both missing and incorrect tokens so
    callers can't distinguish ("does this token exist?" vs "is mine wrong?").
    Comparison uses :func:`secrets.compare_digest` to avoid timing leaks.
    """
    if not API_TOKEN:
        return  # auth disabled (verified safe at startup by ensure_auth_configured)

    expected = _BEARER_PREFIX + API_TOKEN
    provided = authorization or ""
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
