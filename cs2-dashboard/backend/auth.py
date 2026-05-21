"""Bearer-token auth for /api/* routes.

One shared token for one user. /api/health stays public — it carries no
data and is useful as an unauthenticated liveness probe.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from config import settings


def require_auth(authorization: str = Header(default="")) -> None:
    """FastAPI dependency: reject requests without the shared bearer token."""
    expected = f"Bearer {settings.api_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
