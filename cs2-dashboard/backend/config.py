"""Environment-backed configuration for the dashboard backend.

Loads backend/.env if present; real environment variables always win.
Fails fast at import time when a required value is missing — better to
crash on boot than to serve a half-configured API.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.is_file():
    load_dotenv(_ENV_PATH)


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_role: str
    api_token: str
    login_password: str
    pipeline_repo_path: str
    allowed_origin: str
    log_level: str
    port: int


def _get(name: str, default: str = "", *, required: bool = False) -> str:
    val = os.getenv(name, default).strip()
    if required and not val:
        raise RuntimeError(f"missing required env var: {name}")
    return val


def load_settings() -> Settings:
    return Settings(
        supabase_url=_get("SUPABASE_URL", required=True).rstrip("/"),
        supabase_service_role=_get("SUPABASE_SERVICE_ROLE", required=True),
        api_token=_get("DASHBOARD_API_TOKEN", required=True),
        login_password=_get("DASHBOARD_LOGIN_PASSWORD"),
        pipeline_repo_path=_get("PIPELINE_REPO_PATH"),
        allowed_origin=_get("ALLOWED_ORIGIN", "*") or "*",
        log_level=_get("LOG_LEVEL", "INFO") or "INFO",
        port=int(_get("PORT", "8001") or "8001"),
    )


settings = load_settings()
