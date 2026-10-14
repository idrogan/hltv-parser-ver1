"""Fan-out over several entities in several registries.

Errors on one entity never sink the whole batch: they are returned in
``errors`` so the n8n workflow can still store what succeeded and alert
on what failed.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from .client import HttpClient, RegistryError
from .sources import REGISTRIES, Brreg, CompaniesHouse, DanishCVR, RomaniaAnaf, SecEdgar

log = logging.getLogger(__name__)


def _default_user_agent() -> str:
    # SEC rejects requests without a contact in the UA; the others just like it.
    return os.getenv("REGISTRY_USER_AGENT") or "esports-data-api registry monitor (set REGISTRY_USER_AGENT)"


class RegistryService:
    def __init__(
        self,
        companies_house_key: Optional[str] = None,
        user_agent: Optional[str] = None,
        min_delay: float = 0.5,
        http: Optional[HttpClient] = None,
    ):
        http = http or HttpClient(user_agent or _default_user_agent(), min_delay=min_delay)
        self.sources = {
            "gb_ch": CompaniesHouse(http, companies_house_key),
            "dk_cvr": DanishCVR(http),
            "no_brreg": Brreg(http),
            "us_sec": SecEdgar(http),
            "ro_anaf": RomaniaAnaf(http),
        }

    def filings(self, registry: str, entity_id: str, since: Optional[str] = None, limit: int = 50) -> list[dict]:
        if registry not in self.sources:
            raise RegistryError(f"unknown registry {registry!r}; expected one of {REGISTRIES}")
        rows = self.sources[registry].filings(entity_id, limit=limit)
        if since:
            # Rows without a date (Brreg) are always kept: dedupe happens downstream on filing_uid.
            rows = [r for r in rows if not r.get("filed_at") or r["filed_at"] >= since]
        return rows

    def batch(self, entities: list[dict], since: Optional[str] = None, limit: int = 50) -> dict:
        filings: list[dict] = []
        errors: list[dict] = []
        for e in entities:
            registry, entity_id = e.get("registry"), str(e.get("entity_id", "")).strip()
            try:
                rows = self.filings(registry, entity_id, since=since, limit=limit)
            except RegistryError as exc:
                log.warning("registry %s %s failed: %s", registry, entity_id, exc)
                errors.append({"registry": registry, "entity_id": entity_id, "error": str(exc)})
                continue
            for r in rows:
                if e.get("legal_entity_id") is not None:
                    r["legal_entity_id"] = e["legal_entity_id"]
                if e.get("name"):
                    r["entity_name"] = e["name"]
            filings.extend(rows)
        filings.sort(key=lambda r: r.get("filed_at") or "", reverse=True)
        return {"since": since, "count": len(filings), "filings": filings, "errors": errors}

    def brreg_accounts(self, orgnr: str) -> list[dict]:
        return self.sources["no_brreg"].accounts(orgnr)

    # metric names in the normalized fact format, for registries returning one row per period
    _BRREG_FIELDS = {
        "revenue": "revenue", "operating_result": "operating_result", "net_result": "net_result",
        "equity": "equity", "total_assets": "total_assets",
    }

    def accounts(self, registry: str, entity_id: str,
                 from_year: Optional[int] = None, to_year: Optional[int] = None) -> list[dict]:
        """Key figures as normalized facts (one row per period x metric)."""
        if registry == "ro_anaf":
            return self.sources["ro_anaf"].accounts(entity_id, from_year=from_year, to_year=to_year)
        if registry == "no_brreg":
            facts = []
            for row in self.brreg_accounts(entity_id):
                year = int((row.get("period_end") or "0000")[:4] or 0)
                if (from_year and year < from_year) or (to_year and year > to_year):
                    continue
                for field, metric in self._BRREG_FIELDS.items():
                    if row.get(field) is not None:
                        facts.append({
                            "entity_id": row["entity_id"], "period_end": row["period_end"], "metric": metric,
                            "value": row[field], "currency": row.get("currency"), "label": field,
                            "source_url": row.get("source_url"),
                        })
            return facts
        raise RegistryError(f"registry {registry!r} does not expose figures; use /filings")
