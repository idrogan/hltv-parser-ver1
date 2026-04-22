"""Public-internet hardening: per-IP rate limiting + redacted access log.

Both pieces are wired into the FastAPI app from ``app.py``.

Why a custom IP resolver
------------------------
Render (and basically every PaaS) puts a reverse proxy in front of the
container. ``request.client.host`` therefore reports the proxy's address,
not the real caller. The proxy forwards the original IP in
``X-Forwarded-For``; we read that header and fall back to ``request.client``
only when it is absent (e.g. local docker-compose).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable

from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

log = logging.getLogger("access")

RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))


def real_ip(request: Request) -> str:
    """Resolve the caller's real IP behind Render's reverse proxy.

    ``X-Forwarded-For`` is a comma-separated list of hops; the leftmost
    entry is the original client. We trim whitespace and accept the first
    non-empty value. Spoofable from outside Render's edge, but Render
    rewrites the header for inbound traffic so the leftmost value is
    the real edge-observed client.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client:
        return request.client.host
    return "unknown"


limiter = Limiter(
    key_func=real_ip,
    default_limits=[f"{RATE_LIMIT_PER_MINUTE}/minute"],
    headers_enabled=True,  # emit X-RateLimit-* response headers
)
# slowapi's SlowAPIMiddleware catches RateLimitExceeded internally and
# returns its own 429 JSON ({"error": "Rate limit exceeded: N per 1 minute"})
# without bubbling the exception to FastAPI's handler dispatcher. We accept
# that default shape — the wording is informative and the status code is
# what matters for clients.


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log every request as a single line; never log Authorization values."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.exception(
                "%s %s ip=%s status=500 duration_ms=%d",
                request.method,
                request.url.path,
                real_ip(request),
                duration_ms,
            )
            raise
        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "%s %s ip=%s status=%d duration_ms=%d",
            request.method,
            request.url.path,
            real_ip(request),
            status,
            duration_ms,
        )
        return response


def install(app: FastAPI) -> None:
    """Attach rate limiter + access logger to the FastAPI app."""
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(AccessLogMiddleware)
