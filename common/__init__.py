"""Shared auth + error-shape helpers used by every source router."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Header, HTTPException

API_TOKEN = os.getenv("API_TOKEN") or None


def bearer_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """Reject requests whose token doesn't match ``API_TOKEN``.

    No-op when ``API_TOKEN`` is unset, which is convenient on a private
    LAN but unsafe on anything public.
    """
    if not API_TOKEN:
        return
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid token")
