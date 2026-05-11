"""Lifetime price history scraper from Steam Market listing HTML.

Every Steam Market item page embeds the full sales history as a JS array:

    var line1 = [["Oct 21 2021 01: +0", 0.351, "816"], ...];

Each tuple is ``("MON DD YYYY HH: +0", price_usd, volume_as_str)``. The
hour suffix ``+0`` is Valve's UTC marker. Granularity starts hourly,
collapses to weekly/monthly aggregates for older points (Valve decides
when to re-bucket).

This module fetches that page, extracts the array, and aggregates points
into per-day rows ready for ``sticker_price_history``.

Why a separate client from ``steam_market/client.py``: that one is a
JSON client (``priceoverview`` etc.) with its own UA and headers tuned
for the API surface. This one needs a normal browser-y request to a
plain HTML page, and Valve is stricter here — a 5-second floor between
requests is required to stay alive across a 200-sticker walk.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
import urllib.parse
from datetime import datetime
from statistics import median
from typing import Iterable

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

_LINE1_RE = re.compile(r"var\s+line1\s*=\s*(\[.*?\])\s*;", re.S)
_POINT_DT_RE = re.compile(r"^(\w+ \d+ \d{4}) (\d+): \+\d+$")

# Three browser UAs rotated per request — Steam will Cf-challenge a
# constant UA over a long backfill walk.
_USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)


class SteamHistoryError(Exception):
    """Permanent failure (404 delisted item, parse failed, ...)."""


class SteamHistoryBlocked(SteamHistoryError):
    """Retryable: 429 / 5xx / network error."""


def _parse_listing_history(html: str) -> list[tuple[datetime, float, int]]:
    """Pull the ``var line1 = [...]`` array out of the listing HTML."""
    m = _LINE1_RE.search(html)
    if not m:
        return []
    arr = json.loads(m.group(1))
    out: list[tuple[datetime, float, int]] = []
    for dt_str, price, vol_str in arr:
        dm = _POINT_DT_RE.match(dt_str)
        if not dm:
            continue
        ts = datetime.strptime(f"{dm.group(1)} {dm.group(2)}", "%b %d %Y %H")
        try:
            out.append((ts, float(price), int(vol_str)))
        except (TypeError, ValueError):
            continue
    return out


def aggregate_to_daily(points: Iterable[tuple[datetime, float, int]]) -> list[dict]:
    """Bucket intra-day points by calendar date.

    For each date, ``lowest_price_usd`` is the min observed price,
    ``median_price_usd`` is the median, and ``volume`` is the sum of
    sales counts. This is what downstream charts want — Steam's
    intra-day noise smoothed into one row per (item, date).
    """
    by_date: dict = {}
    for ts, price, vol in points:
        d = ts.date()
        bucket = by_date.setdefault(d, {"prices": [], "volume": 0})
        bucket["prices"].append(price)
        bucket["volume"] += vol
    return [
        {
            "date": d,
            "lowest_price_usd": round(min(b["prices"]), 4),
            "median_price_usd": round(median(b["prices"]), 4),
            "volume": b["volume"],
        }
        for d, b in sorted(by_date.items())
    ]


class SteamHistoryClient:
    """Polite HTML-page client for Steam Market listings.

    ``min_delay`` is a hard floor — even with backoff, two requests
    never fire closer than this. Default 5 s matches the non-negotiables
    in the project spec.
    """

    BASE_URL = "https://steamcommunity.com/market/listings/730/"

    def __init__(self, min_delay: float = 5.0, timeout: int = 30):
        self.min_delay = min_delay
        self.timeout = timeout
        self._last_request_at = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.min_delay - elapsed
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.6))
            self._last_request_at = time.monotonic()

    @retry(
        reraise=True,
        retry=retry_if_exception_type(SteamHistoryBlocked),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=3, min=3, max=60),
    )
    def fetch_history(self, market_hash_name: str) -> list[dict]:
        """Return per-day aggregates for ``market_hash_name``.

        Returns ``[]`` for items with no sales recorded (rare — usually
        delisted or pre-launch). Raises ``SteamHistoryError`` on a
        permanent failure (404), or after exhausting retries on a
        transient one.
        """
        self._throttle()
        url = self.BASE_URL + urllib.parse.quote(market_hash_name, safe="")
        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            r = httpx.get(url, headers=headers, timeout=self.timeout, follow_redirects=True)
        except Exception as exc:
            raise SteamHistoryBlocked(f"network error: {exc}") from exc

        if r.status_code in (429, 502, 503):
            raise SteamHistoryBlocked(
                f"steam status={r.status_code} for {market_hash_name!r}"
            )
        if r.status_code == 404:
            raise SteamHistoryError(f"steam 404 — item not found: {market_hash_name!r}")
        if r.status_code != 200:
            raise SteamHistoryError(
                f"unexpected status {r.status_code} for {market_hash_name!r}"
            )

        points = _parse_listing_history(r.text)
        if not points:
            log.warning("no priceHistory data on listing page for %r", market_hash_name)
            return []
        return aggregate_to_daily(points)
