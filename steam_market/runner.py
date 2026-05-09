"""End-to-end Steam Market price refresh.

Reads ``watched_items`` from Supabase, calls the existing
``SteamMarketService.price_overview`` for each (throttled by the
client's ``STEAM_MIN_DELAY``), appends one row per item to
``steam_prices``. Bracketed by a ``_scraper_runs`` row.

Idempotency note: ``steam_prices`` is intentionally append-only —
each run adds a snapshot. Trends are queried by
``(market_hash_name, fetched_at desc)``.
"""
from __future__ import annotations

import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Optional

from common.sb_writer import SupabaseWriter
from .client import SteamClient, SteamError
from .service import SteamMarketService

log = logging.getLogger(__name__)


def _select_watched_items(
    writer: SupabaseWriter,
    *,
    event_slug: Optional[str] = None,
    appid: Optional[int] = None,
    limit: int = 500,
) -> list[dict]:
    """Read watched_items with optional filters. Uses raw httpx to keep
    the SupabaseWriter API surface small."""
    params = {"select": "market_hash_name,appid,event_slug,category", "limit": limit}
    if event_slug:
        params["event_slug"] = f"eq.{event_slug}"
    if appid is not None:
        params["appid"] = f"eq.{appid}"
    r = writer._client.get("/watched_items", params=params)
    r.raise_for_status()
    return r.json()


def run_once(
    *,
    event_slug: Optional[str] = None,
    appid: Optional[int] = None,
    currency: int = 1,
    min_delay: float = float(os.getenv("STEAM_MIN_DELAY", "3.5")),
    proxy: Optional[str] = os.getenv("STEAM_PROXY") or None,
    max_items: int = 200,
) -> dict:
    writer = SupabaseWriter()
    steam = SteamMarketService(SteamClient(min_delay=min_delay, proxy=proxy))

    items = _select_watched_items(
        writer, event_slug=event_slug, appid=appid, limit=max_items
    )
    if not items:
        log.warning("event=steam_no_watched event_slug=%s appid=%s", event_slug, appid)

    rows_written = 0
    errors = 0
    run_id = writer.start_run(
        "steam_prices",
        meta={"event_slug": event_slug, "appid": appid, "n_items": len(items)},
    )

    try:
        out_rows: list[dict] = []
        for item in items:
            name = item["market_hash_name"]
            item_appid = item.get("appid") or 730
            try:
                p = steam.price_overview(name, appid=item_appid, currency=currency)
            except SteamError as e:
                errors += 1
                log.warning("event=steam_item_error name=%r error=%r", name, e)
                continue
            # Skip rows with no usable signal at all.
            if p["lowest_price"] is None and p["median_price"] is None and p["volume_24h"] is None:
                log.warning("event=steam_item_empty name=%r raw=%r", name, p)
                continue
            out_rows.append({
                "market_hash_name": name,
                "lowest_price": p["lowest_price"],
                "median_price": p["median_price"],
                "volume_24h": p["volume_24h"],
                "currency": currency,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })

        if out_rows:
            writer.insert("steam_prices", out_rows, returning=False)
            rows_written = len(out_rows)

        writer.finish_run_ok(
            run_id,
            rows_written=rows_written,
            meta={
                "event_slug": event_slug,
                "appid": appid,
                "n_items_requested": len(items),
                "n_rows_written": rows_written,
                "n_errors": errors,
            },
        )
        return {
            "ok": True,
            "run_id": run_id,
            "rows_written": rows_written,
            "errors": errors,
            "items": len(items),
        }
    except Exception as e:
        tb = traceback.format_exc()
        writer.finish_run_error(
            run_id,
            error=tb,
            rows_written=rows_written,
            meta={"event_slug": event_slug, "appid": appid, "n_errors": errors},
        )
        log.exception("event=steam_run_failed run_id=%s", run_id)
        return {"ok": False, "run_id": run_id, "error": str(e)}
    finally:
        writer.close()


def seed_watched_items(
    names: list[str],
    *,
    event_slug: Optional[str] = None,
    category: Optional[str] = None,
    appid: int = 730,
) -> dict:
    """Bulk-upsert into watched_items. ``market_hash_name`` is the PK,
    so re-seeding the same name is idempotent (it just refreshes
    metadata)."""
    rows = [
        {
            "market_hash_name": n.strip(),
            "appid": appid,
            "event_slug": event_slug,
            "category": category,
        }
        for n in names
        if n.strip()
    ]
    if not rows:
        return {"inserted": 0, "skipped": "empty input"}
    with SupabaseWriter() as writer:
        written = writer.upsert(
            "watched_items", rows, on_conflict="market_hash_name"
        )
    return {"inserted": len(written), "event_slug": event_slug, "category": category}
