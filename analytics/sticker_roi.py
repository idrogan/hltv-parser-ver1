"""Aggregations + ROI math for the sticker price history pipeline.

Inputs come from two tables in Supabase:
  * ``sticker_catalog``                — (market_hash_name → event, category, tier)
  * ``sticker_price_history_blended``  — (market_hash_name, date) → price (USD)

Outputs:
  * ``daily_basket(event, category, placement_tier)`` — one median price
    per calendar date across every item in that bucket. This is the
    series the line-chart layer plots.
  * ``roi_table(event, category, placement_tier)``  — three buyer
    profiles (launch / 30-day / 1-year), each with buy_price,
    today_price, roi_pct. This is the headline number for the post.

Why bucket-median first, then ROI: each ROI cell is the central
tendency of a basket. A single autograph 10x-ing is not the story —
the story is "the median Atlanta 2017 Foil sticker did X%". Computing
ROI per-item-then-mean would let outliers dominate; bucket-median
first then per-bucket ROI keeps the chart honest.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Iterable

import httpx

log = logging.getLogger(__name__)


# Number of trailing days used when computing "today's price" — smooths
# over single-day spikes and zero-volume days where price can wobble.
TODAY_WINDOW_DAYS = 7

# Buyer-profile windows, in days from earliest observed date for the bucket.
BUYER_PROFILES = {
    "launch":   (0,   6),
    "30-day":   (30,  36),
    "1-year":   (365, 371),
}


# ----------------------------------------------------------------------------
# Supabase reads
# ----------------------------------------------------------------------------

def _sb_get(path: str, params: dict) -> list[dict]:
    key = os.environ["SUPABASE_SERVICE_ROLE"]
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    # PostgREST defaults to 1000-row limit. We expect at most ~500k
    # rows across both events, so we page with Range headers.
    out: list[dict] = []
    offset = 0
    page = 5000
    while True:
        r = httpx.get(
            f"{base}/{path}",
            params=params,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Range-Unit": "items",
                "Range": f"{offset}-{offset + page - 1}",
            },
            timeout=60,
        )
        r.raise_for_status()
        chunk = r.json()
        out.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
    return out


def _fetch_catalog(event_slug: str) -> dict[str, dict]:
    rows = _sb_get("sticker_catalog", {
        "select": "market_hash_name,category,team_name,player_name,placement_tier",
        "event_slug": f"eq.{event_slug}",
    })
    return {r["market_hash_name"]: r for r in rows}


def _fetch_history(item_names: Iterable[str]) -> list[dict]:
    # PostgREST `in.(...)` is comma-separated; values quoted via `""`.
    names_list = list(item_names)
    if not names_list:
        return []
    quoted = ",".join('"' + n.replace('"', '\\"') + '"' for n in names_list)
    return _sb_get("sticker_price_history_blended", {
        "select": "market_hash_name,date,median_price_usd,volume",
        "market_hash_name": f"in.({quoted})",
    })


# ----------------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------------

@dataclass
class Bucket:
    """One bucket = one line on the chart. Keyed by (category, tier)."""
    category: str
    placement_tier: str
    daily_median: dict[date, float]   # date → median across items that day
    item_count: int                   # how many items contribute
    earliest_date: date
    latest_date: date


def aggregate(event_slug: str) -> dict[tuple[str, str], Bucket]:
    """Returns ``{(category, placement_tier): Bucket}``."""
    catalog = _fetch_catalog(event_slug)
    if not catalog:
        raise RuntimeError(
            f"sticker_catalog has zero rows for event_slug={event_slug!r}. "
            "Seed first: `python cli.py sticker-catalog seed --event-slug X`."
        )
    history = _fetch_history(catalog.keys())
    if not history:
        raise RuntimeError(
            f"sticker_price_history_blended has zero rows for {event_slug!r}. "
            "Backfill first: `python cli.py steam-history backfill --event-slug X`."
        )

    # group: (category, tier, date) → [prices across items]
    bag: dict[tuple[str, str, date], list[float]] = defaultdict(list)
    items_in_bucket: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in history:
        name = row["market_hash_name"]
        meta = catalog.get(name)
        if meta is None:
            continue
        price = row.get("median_price_usd")
        if price is None:
            continue
        d = date.fromisoformat(row["date"])
        key3 = (meta["category"], meta["placement_tier"], d)
        bag[key3].append(float(price))
        items_in_bucket[(meta["category"], meta["placement_tier"])].add(name)

    buckets: dict[tuple[str, str], Bucket] = {}
    by_bucket: dict[tuple[str, str], dict[date, float]] = defaultdict(dict)
    for (cat, tier, d), prices in bag.items():
        by_bucket[(cat, tier)][d] = round(median(prices), 4)

    for (cat, tier), series in by_bucket.items():
        dates = sorted(series.keys())
        buckets[(cat, tier)] = Bucket(
            category=cat,
            placement_tier=tier,
            daily_median=dict(sorted(series.items())),
            item_count=len(items_in_bucket[(cat, tier)]),
            earliest_date=dates[0],
            latest_date=dates[-1],
        )
    return buckets


# ----------------------------------------------------------------------------
# ROI
# ----------------------------------------------------------------------------

@dataclass
class ROICell:
    profile: str          # 'launch' | '30-day' | '1-year'
    buy_window_dates: tuple[date, date]
    buy_price: float
    today_price: float
    roi_pct: float
    n_items: int          # items contributing to the bucket


def _window_median(series: dict[date, float], start: date, end: date) -> float | None:
    """Median of a series restricted to ``[start, end]`` inclusive."""
    sample = [v for d, v in series.items() if start <= d <= end]
    return round(median(sample), 4) if sample else None


def roi_table(buckets: dict[tuple[str, str], Bucket]) -> list[dict]:
    """Returns a flat list of dicts, one row per (category, tier, profile)."""
    rows = []
    for (cat, tier), b in buckets.items():
        # "Today" = trailing 7-day median ending at the bucket's latest date.
        today_end = b.latest_date
        today_start = today_end - timedelta(days=TODAY_WINDOW_DAYS - 1)
        today_price = _window_median(b.daily_median, today_start, today_end)
        if today_price is None or today_price == 0:
            continue

        for profile, (lo, hi) in BUYER_PROFILES.items():
            window_start = b.earliest_date + timedelta(days=lo)
            window_end   = b.earliest_date + timedelta(days=hi)
            buy = _window_median(b.daily_median, window_start, window_end)
            if buy is None or buy == 0:
                # Capsule may have launched, but the bucket has no
                # samples in this window — skip rather than report nonsense.
                continue
            roi_pct = round((today_price - buy) / buy * 100, 1)
            rows.append({
                "category": cat,
                "placement_tier": tier,
                "profile": profile,
                "buy_window_start": window_start.isoformat(),
                "buy_window_end":   window_end.isoformat(),
                "buy_price_usd":    buy,
                "today_price_usd":  today_price,
                "roi_pct":          roi_pct,
                "n_items":          b.item_count,
            })
    # Sort: category, then tier (top3 first), then profile order
    profile_order = {"launch": 0, "30-day": 1, "1-year": 2}
    tier_order    = {"champion": 0, "finalist": 1, "top3": 2, "rest": 3}
    rows.sort(key=lambda r: (
        r["category"], tier_order.get(r["placement_tier"], 9),
        profile_order.get(r["profile"], 9),
    ))
    return rows


def summary(event_slug: str) -> dict:
    """One-shot helper for CLI: aggregate + ROI, returned as plain dicts."""
    buckets = aggregate(event_slug)
    return {
        "event_slug": event_slug,
        "buckets": [
            {
                "category": b.category,
                "placement_tier": b.placement_tier,
                "item_count": b.item_count,
                "earliest_date": b.earliest_date.isoformat(),
                "latest_date":   b.latest_date.isoformat(),
                "n_days":        len(b.daily_median),
            }
            for b in buckets.values()
        ],
        "roi": roi_table(buckets),
    }
