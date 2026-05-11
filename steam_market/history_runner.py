"""Walk a sticker catalog (or an ad-hoc name list), fetch lifetime
Steam Market history for each item, upsert daily rows into
``sticker_price_history``.

The runner has two input modes:

  * ``--event-slug`` — reads ``sticker_catalog`` filtered by event,
    used for full Phase-3 backfills.
  * ``--names`` — comma-separated list of market_hash_name values,
    used for smoke tests before the catalog is seeded.

Upserts go through PostgREST with ``Prefer: resolution=merge-duplicates``
keyed on the table's ``(market_hash_name, date, source)`` unique
constraint, so re-running a backfill never duplicates rows.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Iterable

import httpx

from steam_market.history import (
    SteamHistoryClient,
    SteamHistoryError,
)

log = logging.getLogger(__name__)

_CHUNK = 500   # PostgREST request size for sticker_price_history inserts
_SOURCE = "steam_market"


def _sb_headers() -> dict:
    key = os.environ["SUPABASE_SERVICE_ROLE"]
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def _sb_base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"


def _fetch_catalog_names(event_slug: str) -> list[str]:
    r = httpx.get(
        f"{_sb_base()}/sticker_catalog",
        params={"select": "market_hash_name", "event_slug": f"eq.{event_slug}"},
        headers={"apikey": os.environ["SUPABASE_SERVICE_ROLE"],
                 "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_ROLE']}"},
        timeout=30,
    )
    r.raise_for_status()
    return [row["market_hash_name"] for row in r.json()]


def _upsert_history(rows: list[dict]) -> int:
    if not rows:
        return 0
    written = 0
    for i in range(0, len(rows), _CHUNK):
        batch = rows[i : i + _CHUNK]
        r = httpx.post(
            f"{_sb_base()}/sticker_price_history?on_conflict=market_hash_name,date,source",
            json=batch,
            headers=_sb_headers(),
            timeout=60,
        )
        if r.status_code not in (200, 201, 204):
            log.error("upsert failed: %s %s", r.status_code, r.text[:300])
            r.raise_for_status()
        written += len(batch)
    return written


def backfill(
    *,
    event_slug: str | None = None,
    names: Iterable[str] | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    min_delay: float = 5.0,
) -> dict:
    """Fetch lifetime history for every item, upsert daily rows.

    Exactly one of ``event_slug`` / ``names`` must be set.

    Returns a summary dict with ``items_total``, ``items_ok``,
    ``items_failed``, and ``rows_written``.
    """
    if (event_slug is None) == (names is None):
        raise ValueError("pass exactly one of event_slug / names")

    if event_slug is not None:
        item_names = _fetch_catalog_names(event_slug)
        if not item_names:
            raise RuntimeError(
                f"sticker_catalog is empty for event_slug={event_slug!r}. "
                "Seed it first (Phase 1.4)."
            )
    else:
        item_names = list(names)  # type: ignore[arg-type]

    if limit:
        item_names = item_names[:limit]

    client = SteamHistoryClient(min_delay=min_delay)
    items_ok = items_failed = rows_written = 0
    started = time.monotonic()

    for i, name in enumerate(item_names, 1):
        log.info("[%d/%d] %s", i, len(item_names), name)
        try:
            daily = client.fetch_history(name)
        except SteamHistoryError as exc:
            log.warning("  → fail: %s", exc)
            items_failed += 1
            continue

        if not daily:
            log.info("  → no history data")
            items_failed += 1
            continue

        rows = [
            {
                "market_hash_name": name,
                "date": d["date"].isoformat(),
                "source": _SOURCE,
                "lowest_price_usd": d["lowest_price_usd"],
                "median_price_usd": d["median_price_usd"],
                "volume": d["volume"],
            }
            for d in daily
        ]

        if dry_run:
            log.info("  → dry-run: would write %d rows (%s → %s)",
                     len(rows), rows[0]["date"], rows[-1]["date"])
            rows_written += len(rows)  # for reporting only
            items_ok += 1
            continue

        written = _upsert_history(rows)
        log.info("  → wrote %d rows (%s → %s)",
                 written, rows[0]["date"], rows[-1]["date"])
        rows_written += written
        items_ok += 1

    elapsed = time.monotonic() - started
    return {
        "event_slug": event_slug,
        "items_total": len(item_names),
        "items_ok": items_ok,
        "items_failed": items_failed,
        "rows_written": rows_written,
        "elapsed_sec": round(elapsed, 1),
        "dry_run": dry_run,
    }
