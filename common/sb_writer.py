"""Tiny Supabase writer over PostgREST.

Why not the official `supabase-py` SDK? Two reasons:
  * It pulls in `realtime` + websockets, which we don't need.
  * PostgREST is a stable HTTP/JSON contract; a 60-line wrapper is
    easier to reason about than someone else's abstraction.

Auth: service-role key (`SUPABASE_SERVICE_ROLE`). It bypasses RLS,
which is what we want for a backend writer. Treat the key as top-tier
secret — it must live in `.env` / platform env vars, never in code.

Conventions:
  * Every parser run starts with `start_run(name, meta)` and ends with
    either `finish_run_ok(...)` or `finish_run_error(...)`. This is the
    single source of truth for "is the pipeline alive".
  * Upserts use `on_conflict='source,source_id'` so the same logical
    entity from a single source doesn't duplicate. Cross-source dedup
    is Phase 2.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Optional

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)


class SupabaseConfigError(RuntimeError):
    """Raised when SUPABASE_URL / SUPABASE_SERVICE_ROLE are missing."""


class SupabaseWriteError(RuntimeError):
    """Raised on a non-2xx response from PostgREST."""


def _required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise SupabaseConfigError(
            f"Environment variable {name} is required for Supabase writes "
            "but is not set. Check your .env."
        )
    return val


class SupabaseWriter:
    def __init__(
        self,
        url: Optional[str] = None,
        service_role: Optional[str] = None,
    ):
        self.base = (url or _required("SUPABASE_URL")).rstrip("/")
        key = service_role or _required("SUPABASE_SERVICE_ROLE")
        self._client = httpx.Client(
            base_url=f"{self.base}/rest/v1",
            timeout=_TIMEOUT,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept-Encoding": "gzip",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- core ops --------------------------------------------------------

    def upsert(
        self,
        table: str,
        rows: Iterable[dict],
        on_conflict: str,
        returning: bool = True,
    ) -> list[dict]:
        """POST with Prefer: resolution=merge-duplicates.

        Returns the upserted rows when ``returning=True`` so callers can
        capture freshly-assigned ``id`` values for foreign keys.
        """
        rows = list(rows)
        if not rows:
            return []
        prefer = "resolution=merge-duplicates"
        prefer += ",return=representation" if returning else ",return=minimal"
        r = self._client.post(
            f"/{table}",
            params={"on_conflict": on_conflict},
            json=rows,
            headers={"Prefer": prefer},
        )
        if r.status_code >= 300:
            raise SupabaseWriteError(
                f"upsert {table} failed: {r.status_code} {r.text[:300]}"
            )
        return r.json() if returning else []

    def insert(self, table: str, rows: Iterable[dict], returning: bool = True) -> list[dict]:
        rows = list(rows)
        if not rows:
            return []
        prefer = "return=representation" if returning else "return=minimal"
        r = self._client.post(
            f"/{table}",
            json=rows,
            headers={"Prefer": prefer},
        )
        if r.status_code >= 300:
            raise SupabaseWriteError(
                f"insert {table} failed: {r.status_code} {r.text[:300]}"
            )
        return r.json() if returning else []

    def select(self, table: str, *, select: str = "*", **filters: Any) -> list[dict]:
        """Simple eq-filter select. ``filters['source']='liquipedia'`` →
        ``?source=eq.liquipedia``. For more complex queries hit
        PostgREST directly."""
        params = {"select": select}
        for k, v in filters.items():
            params[k] = f"eq.{v}"
        r = self._client.get(f"/{table}", params=params)
        if r.status_code >= 300:
            raise SupabaseWriteError(
                f"select {table} failed: {r.status_code} {r.text[:300]}"
            )
        return r.json()

    def delete(self, table: str, match: dict) -> int:
        """DELETE rows matching simple eq filters. Returns row count."""
        params = {k: f"eq.{v}" for k, v in match.items()}
        r = self._client.delete(
            f"/{table}",
            params=params,
            headers={"Prefer": "return=representation"},
        )
        if r.status_code >= 300:
            raise SupabaseWriteError(
                f"delete {table} failed: {r.status_code} {r.text[:300]}"
            )
        return len(r.json()) if r.text else 0

    def update(
        self,
        table: str,
        match: dict,
        patch: dict,
        returning: bool = False,
    ) -> list[dict]:
        params = {k: f"eq.{v}" for k, v in match.items()}
        prefer = "return=representation" if returning else "return=minimal"
        r = self._client.patch(
            f"/{table}",
            params=params,
            json=patch,
            headers={"Prefer": prefer},
        )
        if r.status_code >= 300:
            raise SupabaseWriteError(
                f"update {table} failed: {r.status_code} {r.text[:300]}"
            )
        return r.json() if returning else []

    # ---- _scraper_runs helpers ------------------------------------------

    def start_run(self, scraper_name: str, meta: Optional[dict] = None) -> int:
        row = self.insert(
            "_scraper_runs",
            [{"scraper_name": scraper_name, "status": "running", "meta": meta or {}}],
        )[0]
        log.info(
            "event=scraper_run_start scraper=%s run_id=%s", scraper_name, row["id"]
        )
        return int(row["id"])

    def finish_run_ok(
        self,
        run_id: int,
        rows_written: int,
        meta: Optional[dict] = None,
    ) -> None:
        from datetime import datetime, timezone
        patch = {
            "status": "ok",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "rows_written": rows_written,
        }
        if meta is not None:
            patch["meta"] = meta
        self.update("_scraper_runs", {"id": run_id}, patch)
        log.info(
            "event=scraper_run_ok run_id=%s rows=%s", run_id, rows_written
        )

    def finish_run_error(
        self,
        run_id: int,
        error: str,
        rows_written: int = 0,
        meta: Optional[dict] = None,
    ) -> None:
        from datetime import datetime, timezone
        patch = {
            "status": "error",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "rows_written": rows_written,
            "error": error[:4000],
        }
        if meta is not None:
            patch["meta"] = meta
        self.update("_scraper_runs", {"id": run_id}, patch)
        log.warning(
            "event=scraper_run_error run_id=%s error=%r", run_id, error[:200]
        )
