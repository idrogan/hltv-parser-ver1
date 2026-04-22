"""Combined FastAPI app: HLTV + Steam Market + EsportsCharts.

Mount every source under its own prefix so routes can't collide and so
n8n / Make URLs read clearly:

    /hltv/team/{id}/{slug}/maps
    /steam/price
    /escharts/tournaments

Run locally:
    uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common import ensure_auth_configured
from common.middleware import install as install_middleware
from escharts_parser.router import register_exception_handlers as escharts_handlers
from escharts_parser.router import router as escharts_router
from hltv_parser.router import register_exception_handlers as hltv_handlers
from hltv_parser.router import router as hltv_router
from steam_market.router import register_exception_handlers as steam_handlers
from steam_market.router import router as steam_router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# Fail at import time (= Render build/boot phase) if a public deployment
# forgot to set API_TOKEN. Better to crash the build than to silently
# expose every scraper to the open internet.
ensure_auth_configured()

app = FastAPI(
    title="Esports Data API",
    version="0.3.0",
    description=(
        "JSON wrapper around HLTV, Steam Community Market and "
        "EsportsCharts — designed as Layer 1 of a Make/n8n content "
        "pipeline. All time-windowed HLTV endpoints accept explicit "
        "start_date/end_date (YYYY-MM-DD) or a months_back shortcut."
    ),
)

# Optional CORS, locked down via ALLOWED_ORIGIN. Default '*' keeps
# browser-based callers (form triggers, dev UIs) working but the README
# explicitly recommends restricting in production.
allowed_origin = os.getenv("ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[allowed_origin] if allowed_origin != "*" else ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

install_middleware(app)

for register in (hltv_handlers, steam_handlers, escharts_handlers):
    register(app)

app.include_router(hltv_router)
app.include_router(steam_router)
app.include_router(escharts_router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Public liveness probe — no auth, no rate limit exemption needed."""
    return {"ok": True, "sources": ["hltv", "steam", "escharts"]}


@app.get("/warmup", tags=["meta"])
def warmup() -> dict:
    """Cheap endpoint to wake a Render Free-tier container before heavy work.

    Free-tier services scale to zero after ~15 min idle and take 30-60s
    to cold-boot. Hit ``/warmup`` from a workflow's first step, wait
    ~30-45s, then issue real requests — the container will be warm.
    """
    return {"ok": True, "warm": True}
