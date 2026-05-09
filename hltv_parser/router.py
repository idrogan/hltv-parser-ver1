"""HLTV FastAPI router — mounted by the top-level ``app.py`` under ``/hltv``."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from common import bearer_auth

from .client import HLTVBlockedError, HLTVClient, HLTVError, HLTVPausedError
from .service import HLTVService

MIN_DELAY = float(os.getenv("HLTV_MIN_DELAY", "2.0"))
PROXY = os.getenv("HLTV_PROXY") or None

router = APIRouter(prefix="/hltv", tags=["hltv"])
_service = HLTVService(HLTVClient(min_delay=MIN_DELAY, proxy=PROXY))


def register_exception_handlers(app) -> None:
    """Attach HLTV-specific exception handlers to the parent FastAPI app."""

    @app.exception_handler(HLTVPausedError)
    async def _paused(_, exc: HLTVPausedError):
        return JSONResponse(status_code=503, content={"error": "hltv_paused", "detail": str(exc)})

    @app.exception_handler(HLTVBlockedError)
    async def _blocked(_, exc: HLTVBlockedError):
        return JSONResponse(status_code=503, content={"error": "hltv_blocked", "detail": str(exc)})

    @app.exception_handler(HLTVError)
    async def _err(_, exc: HLTVError):
        return JSONResponse(status_code=502, content={"error": "hltv_error", "detail": str(exc)})


@router.get("/team/{team_id}/{team_slug}/overview", dependencies=[Depends(bearer_auth)])
def team_overview(
    team_id: int,
    team_slug: str,
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    months_back: Optional[int] = Query(None, ge=1, le=60),
) -> dict:
    return _service.team_overview(team_id, team_slug, start_date, end_date, months_back)


@router.get("/team/{team_id}/{team_slug}/maps", dependencies=[Depends(bearer_auth)])
def team_map_stats(
    team_id: int,
    team_slug: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    months_back: Optional[int] = Query(None, ge=1, le=60),
) -> dict:
    """Per-map stats including CT-side and T-side round winrate."""
    return _service.team_map_stats(team_id, team_slug, start_date, end_date, months_back)


@router.get("/team/{team_id}/{team_slug}/matches", dependencies=[Depends(bearer_auth)])
def team_matches(
    team_id: int,
    team_slug: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    months_back: Optional[int] = Query(None, ge=1, le=60),
) -> dict:
    return _service.team_matches(team_id, team_slug, start_date, end_date, months_back)


@router.get("/team/search", dependencies=[Depends(bearer_auth)])
def team_search(name: str = Query(..., min_length=2)) -> dict:
    return {"results": _service.find_team(name)}


@router.get("/player/{player_id}/{player_slug}", dependencies=[Depends(bearer_auth)])
def player_stats(
    player_id: int,
    player_slug: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    months_back: Optional[int] = Query(None, ge=1, le=60),
) -> dict:
    return _service.player_stats(player_id, player_slug, start_date, end_date, months_back)


@router.get("/rankings", dependencies=[Depends(bearer_auth)])
def rankings() -> dict:
    return {"rankings": _service.rankings()}


@router.get("/matches/upcoming", dependencies=[Depends(bearer_auth)])
def upcoming() -> dict:
    return {"matches": _service.upcoming_matches()}


@router.get("/results", dependencies=[Depends(bearer_auth)])
def results(offset: int = Query(0, ge=0)) -> dict:
    return {"results": _service.results(offset=offset)}
