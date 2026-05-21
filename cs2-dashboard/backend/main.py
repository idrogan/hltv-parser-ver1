"""CS2 Dashboard backend — FastAPI app.

Layer 2 of the dashboard: reads Supabase Postgres and (later) triggers
pipeline CLI runs. The frontend talks only to this service, never to
Supabase directly.

Run locally:
    uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001}
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.health import router as health_router
from config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="CS2 Dashboard API",
    version="0.1.0",
    description="Self-hosted CS2 analytics dashboard backend.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.allowed_origin == "*" else [settings.allowed_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health_router)


@app.get("/", tags=["meta"])
def root() -> dict:
    return {"service": "cs2-dashboard-backend", "version": app.version}
