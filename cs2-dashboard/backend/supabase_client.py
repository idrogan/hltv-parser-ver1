"""Read-side httpx wrapper around Supabase PostgREST.

Mirrors the pipeline sb_writer access pattern: plain httpx with the
service_role key, no supabase-py. The dashboard only ever reads, so this
client exposes select/count helpers and a connectivity ping.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import httpx

from config import settings

log = logging.getLogger(__name__)


class SupabaseError(RuntimeError):
    """PostgREST returned an error status or was unreachable."""


@dataclass
class QueryResult:
    rows: list[dict]
    total: int | None = None  # exact row count, populated only when count=True


class SupabaseClient:
    """Thin synchronous PostgREST reader."""

    def __init__(self, url: str, service_role: str, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=url,
            headers={
                "apikey": service_role,
                "Authorization": f"Bearer {service_role}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def ping(self) -> bool:
        """True if PostgREST answers its schema root with valid credentials."""
        try:
            resp = self._client.get("/rest/v1/")
        except httpx.HTTPError as exc:
            log.warning("supabase ping failed: %s", exc)
            return False
        return resp.status_code == 200

    def select(
        self,
        table: str,
        *,
        select: str = "*",
        count: bool = False,
        **query: object,
    ) -> QueryResult:
        """GET rows from a table/view.

        Extra keyword args are passed straight through as PostgREST query
        params, so callers use PostgREST syntax directly, e.g.
        ``select("matches", status="eq.finished", order="scheduled_at.desc",
        limit=20)``. Params whose value is None are dropped.
        """
        params: dict[str, object] = {"select": select}
        params.update({k: v for k, v in query.items() if v is not None})
        headers = {"Prefer": "count=exact"} if count else {}
        try:
            resp = self._client.get(
                f"/rest/v1/{table}", params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise SupabaseError(f"GET {table} failed: {exc}") from exc
        if resp.status_code not in (200, 206):
            raise SupabaseError(
                f"GET {table} -> HTTP {resp.status_code}: {resp.text[:300]}"
            )
        total = None
        if count:
            content_range = resp.headers.get("content-range", "")
            tail = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
            total = int(tail) if tail.isdigit() else None
        return QueryResult(rows=resp.json(), total=total)


@lru_cache(maxsize=1)
def get_supabase() -> SupabaseClient:
    """Process-wide singleton client."""
    return SupabaseClient(settings.supabase_url, settings.supabase_service_role)
