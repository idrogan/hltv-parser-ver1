"""Company-registry router — mounted under ``/registries``.

    GET  /registries/{registry}/{entity_id}/filings?since=YYYY-MM-DD
    POST /registries/filings        {"entities": [...], "since": "YYYY-MM-DD"}
    GET  /registries/no_brreg/{orgnr}/accounts           (raw Brreg rows, kept for compatibility)
    GET  /registries/{registry}/{entity_id}/accounts     (normalized facts; ro_anaf, no_brreg)
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from common import bearer_auth

from .client import RegistryAuthError, RegistryBlockedError, RegistryError
from .service import RegistryService

router = APIRouter(prefix="/registries", tags=["registries"])
_service = RegistryService(
    companies_house_key=os.getenv("COMPANIES_HOUSE_API_KEY") or None,
    user_agent=os.getenv("REGISTRY_USER_AGENT") or None,
    min_delay=float(os.getenv("REGISTRY_MIN_DELAY", "0.5")),
)


def register_exception_handlers(app) -> None:
    @app.exception_handler(RegistryBlockedError)
    async def _blocked(_, exc: RegistryBlockedError):
        return JSONResponse(status_code=503, content={"error": "registry_unavailable", "detail": str(exc)})

    @app.exception_handler(RegistryAuthError)
    async def _auth(_, exc: RegistryAuthError):
        return JSONResponse(status_code=502, content={"error": "registry_auth", "detail": str(exc)})

    @app.exception_handler(RegistryError)
    async def _err(_, exc: RegistryError):
        return JSONResponse(status_code=502, content={"error": "registry_error", "detail": str(exc)})


class EntityRef(BaseModel):
    registry: str = Field(..., description="gb_ch | dk_cvr | no_brreg | us_sec")
    entity_id: str
    legal_entity_id: Optional[int] = None
    name: Optional[str] = None


class BatchRequest(BaseModel):
    entities: list[EntityRef]
    since: Optional[str] = Field(None, description="YYYY-MM-DD; drop filings filed before this date")
    limit: int = Field(50, ge=1, le=200)


@router.post("/filings", dependencies=[Depends(bearer_auth)])
def batch_filings(req: BatchRequest) -> dict:
    return _service.batch([e.model_dump() for e in req.entities], since=req.since, limit=req.limit)


@router.get("/no_brreg/{orgnr}/accounts", dependencies=[Depends(bearer_auth)])
def brreg_accounts(orgnr: str) -> dict:
    return {"registry": "no_brreg", "entity_id": orgnr, "accounts": _service.brreg_accounts(orgnr)}


@router.get("/{registry}/{entity_id}/accounts", dependencies=[Depends(bearer_auth)])
def entity_accounts(
    registry: str,
    entity_id: str,
    from_year: Optional[int] = Query(None, ge=2000, le=2100),
    to_year: Optional[int] = Query(None, ge=2000, le=2100),
) -> dict:
    facts = _service.accounts(registry, entity_id, from_year=from_year, to_year=to_year)
    return {"registry": registry, "entity_id": entity_id, "count": len(facts), "facts": facts}


@router.get("/{registry}/{entity_id}/filings", dependencies=[Depends(bearer_auth)])
def entity_filings(
    registry: str,
    entity_id: str,
    since: Optional[str] = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    rows = _service.filings(registry, entity_id, since=since, limit=limit)
    return {"registry": registry, "entity_id": entity_id, "count": len(rows), "filings": rows}
