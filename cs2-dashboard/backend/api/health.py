"""Health endpoints.

/api/health is public (no auth) — a liveness probe plus a Supabase
connectivity check. The richer /api/health/pipeline aggregate over
_scraper_runs is added in a later step once the live schema is confirmed.
"""
from __future__ import annotations

from fastapi import APIRouter

from supabase_client import get_supabase

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    """Liveness probe plus a Supabase connectivity check. No auth required."""
    return {
        "ok": True,
        "service": "cs2-dashboard-backend",
        "database": {"reachable": get_supabase().ping()},
    }
