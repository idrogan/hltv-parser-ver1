"""FastAPI server exposing the HLTV parser as a JSON API.

Mount this anywhere you can run a Python container — Make and N8N hit it
through the standard HTTP Request module. An optional ``API_TOKEN`` env
var enables ``Authorization: Bearer ...`` checking.

Run locally:
    uvicorn hltv_parser.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from .client import HLTVBlockedError, HLTVClient, HLTVError
from .service import HLTVService

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

API_TOKEN = os.getenv("API_TOKEN")
MIN_DELAY = float(os.getenv("HLTV_MIN_DELAY", "2.0"))
PROXY = os.getenv("HLTV_PROXY") or None

app = FastAPI(
    title="HLTV Parser API",
    version="0.1.0",
    description=(
        "JSON wrapper around HLTV.org pages, designed for Make and N8N "
        "automations. All time-windowed endpoints accept either explicit "
        "start_date/end_date (YYYY-MM-DD) or months_back (e.g. 5)."
    ),
)
service = HLTVService(HLTVClient(min_delay=MIN_DELAY, proxy=PROXY))


def auth(authorization: Optional[str] = Header(default=None)) -> None:
    if not API_TOKEN:
        return
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid token")


@app.exception_handler(HLTVBlockedError)
async def blocked_handler(_, exc: HLTVBlockedError):
    return JSONResponse(status_code=503, content={"error": "hltv_blocked", "detail": str(exc)})


@app.exception_handler(HLTVError)
async def hltv_error_handler(_, exc: HLTVError):
    return JSONResponse(status_code=502, content={"error": "hltv_error", "detail": str(exc)})


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/team/{team_id}/{team_slug}/overview", dependencies=[Depends(auth)])
def team_overview(
    team_id: int,
    team_slug: str,
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    months_back: Optional[int] = Query(None, ge=1, le=60),
) -> dict:
    return service.team_overview(team_id, team_slug, start_date, end_date, months_back)


@app.get("/team/{team_id}/{team_slug}/maps", dependencies=[Depends(auth)])
def team_map_stats(
    team_id: int,
    team_slug: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    months_back: Optional[int] = Query(None, ge=1, le=60),
) -> dict:
    """Per-map stats including CT-side and T-side round winrate.

    Example: ``GET /team/4608/natus-vincere/maps?months_back=5`` returns
    Navi's CT/T winrate per map for the last 5 months.
    """
    return service.team_map_stats(team_id, team_slug, start_date, end_date, months_back)


@app.get("/team/{team_id}/{team_slug}/matches", dependencies=[Depends(auth)])
def team_matches(
    team_id: int,
    team_slug: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    months_back: Optional[int] = Query(None, ge=1, le=60),
) -> dict:
    return service.team_matches(team_id, team_slug, start_date, end_date, months_back)


@app.get("/team/search", dependencies=[Depends(auth)])
def team_search(name: str = Query(..., min_length=2)) -> dict:
    return {"results": service.find_team(name)}


@app.get("/player/{player_id}/{player_slug}", dependencies=[Depends(auth)])
def player_stats(
    player_id: int,
    player_slug: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    months_back: Optional[int] = Query(None, ge=1, le=60),
) -> dict:
    return service.player_stats(player_id, player_slug, start_date, end_date, months_back)


@app.get("/rankings", dependencies=[Depends(auth)])
def rankings() -> dict:
    return {"rankings": service.rankings()}


@app.get("/matches/upcoming", dependencies=[Depends(auth)])
def upcoming() -> dict:
    return {"matches": service.upcoming_matches()}


@app.get("/results", dependencies=[Depends(auth)])
def results(offset: int = Query(0, ge=0)) -> dict:
    return {"results": service.results(offset=offset)}
