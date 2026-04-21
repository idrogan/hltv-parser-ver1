"""EsportsCharts FastAPI router — mounted under ``/escharts``."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from common import bearer_auth

from .client import EsChartsBlockedError, EsChartsClient, EsChartsError
from .service import EsChartsService

MIN_DELAY = float(os.getenv("ESCHARTS_MIN_DELAY", "2.0"))
PROXY = os.getenv("ESCHARTS_PROXY") or None

router = APIRouter(prefix="/escharts", tags=["escharts"])
_service = EsChartsService(EsChartsClient(min_delay=MIN_DELAY, proxy=PROXY))


def register_exception_handlers(app) -> None:
    @app.exception_handler(EsChartsBlockedError)
    async def _blocked(_, exc: EsChartsBlockedError):
        return JSONResponse(status_code=503, content={"error": "escharts_blocked", "detail": str(exc)})

    @app.exception_handler(EsChartsError)
    async def _err(_, exc: EsChartsError):
        return JSONResponse(status_code=502, content={"error": "escharts_error", "detail": str(exc)})


@router.get("/tournaments", dependencies=[Depends(bearer_auth)])
def tournaments(
    game: str = Query("cs2", description="cs2 | csgo | dota2 | lol | valorant | pubg"),
    year: Optional[int] = Query(None, ge=2010, le=2100),
) -> dict:
    return {"game": game, "year": year, "tournaments": _service.tournaments(game=game, year=year)}


@router.get("/tournament/{game}/{slug}", dependencies=[Depends(bearer_auth)])
def tournament(game: str, slug: str) -> dict:
    return _service.tournament(game=game, slug=slug)
