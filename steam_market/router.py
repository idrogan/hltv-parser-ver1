"""Steam market FastAPI router — mounted under ``/steam``."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from common import bearer_auth

from . import CS2_APPID
from .client import SteamBlockedError, SteamClient, SteamError
from .service import SteamMarketService

MIN_DELAY = float(os.getenv("STEAM_MIN_DELAY", "3.5"))
PROXY = os.getenv("STEAM_PROXY") or None
LOGIN_SECURE = os.getenv("STEAM_LOGIN_SECURE") or None

router = APIRouter(prefix="/steam", tags=["steam"])
_service = SteamMarketService(
    SteamClient(min_delay=MIN_DELAY, proxy=PROXY, login_secure_cookie=LOGIN_SECURE)
)


def register_exception_handlers(app) -> None:
    @app.exception_handler(SteamBlockedError)
    async def _blocked(_, exc: SteamBlockedError):
        return JSONResponse(status_code=503, content={"error": "steam_blocked", "detail": str(exc)})

    @app.exception_handler(SteamError)
    async def _err(_, exc: SteamError):
        return JSONResponse(status_code=502, content={"error": "steam_error", "detail": str(exc)})


@router.get("/price", dependencies=[Depends(bearer_auth)])
def price(
    market_hash_name: str = Query(..., description="Exact market name, URL-decoded"),
    appid: int = Query(CS2_APPID),
    currency: int = Query(1, description="Steam currency code; 1 = USD, 3 = EUR, 5 = RUB"),
) -> dict:
    """Current lowest/median price + **24-hour sold volume** for one item.

    Example:
        /steam/price?market_hash_name=Sticker+%7C+Titan+(Holo)+%7C+Katowice+2014
    """
    return _service.price_overview(market_hash_name, appid=appid, currency=currency)


@router.post("/price/bulk", dependencies=[Depends(bearer_auth)])
def price_bulk(
    payload: dict = Body(
        ...,
        examples=[
            {
                "names": [
                    "Sticker | Titan (Holo) | Katowice 2014",
                    "Sticker | iBUYPOWER (Holo) | Katowice 2014",
                ],
                "currency": 1,
                "appid": 730,
            }
        ],
    )
) -> dict:
    names = payload.get("names") or []
    if not isinstance(names, list) or not names:
        return {"items": []}
    items = _service.price_overview_bulk(
        names,
        appid=int(payload.get("appid", CS2_APPID)),
        currency=int(payload.get("currency", 1)),
    )
    return {"items": items}


@router.get("/search", dependencies=[Depends(bearer_auth)])
def search(
    query: str = Query(..., min_length=2),
    appid: int = Query(CS2_APPID),
    count: int = Query(20, ge=1, le=100),
    start: int = Query(0, ge=0),
) -> dict:
    return _service.search(query, appid=appid, count=count, start=start)


@router.get("/history", dependencies=[Depends(bearer_auth)])
def history(
    market_hash_name: str = Query(...),
    appid: int = Query(CS2_APPID),
) -> dict:
    """Full per-sale history. Requires ``STEAM_LOGIN_SECURE`` in env."""
    return _service.price_history(market_hash_name, appid=appid)
