"""Combined FastAPI app: HLTV + Steam Market + EsportsCharts.

Mount every source under its own prefix so routes can't collide and so
n8n / Make URLs read clearly:

    /hltv/team/{id}/{slug}/maps
    /steam/price
    /escharts/tournaments

Run:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from escharts_parser.router import register_exception_handlers as escharts_handlers
from escharts_parser.router import router as escharts_router
from hltv_parser.router import register_exception_handlers as hltv_handlers
from hltv_parser.router import router as hltv_router
from steam_market.router import register_exception_handlers as steam_handlers
from steam_market.router import router as steam_router

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(
    title="Esports Data API",
    version="0.2.0",
    description=(
        "JSON wrapper around HLTV, Steam Community Market and "
        "EsportsCharts — designed as Layer 1 of a Make/n8n content "
        "pipeline. All time-windowed HLTV endpoints accept explicit "
        "start_date/end_date (YYYY-MM-DD) or a months_back shortcut."
    ),
)

for register in (hltv_handlers, steam_handlers, escharts_handlers):
    register(app)

app.include_router(hltv_router)
app.include_router(steam_router)
app.include_router(escharts_router)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "sources": ["hltv", "steam", "escharts"]}
