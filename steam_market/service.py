"""High-level wrappers around the Steam market endpoints."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from .client import SteamClient, SteamError

log = logging.getLogger(__name__)

CS2_APPID = 730
PRICE_NUMBER_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)")


def _parse_price(text: Optional[str]) -> Optional[float]:
    """Turn ``'$1,234.56'`` / ``'1 234,56€'`` / ``'1.234,56 ₽'`` into a float."""
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").strip()
    nums = re.findall(r"[0-9][0-9.,\s]*", cleaned)
    if not nums:
        return None
    raw = nums[0].replace(" ", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        if raw.count(",") == 1 and len(raw.split(",")[-1]) in (1, 2):
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"-?\d[\d,\s]*", text)
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


class SteamMarketService:
    def __init__(self, client: Optional[SteamClient] = None):
        self.client = client or SteamClient()

    def price_overview(
        self,
        market_hash_name: str,
        appid: int = CS2_APPID,
        currency: int = 1,
    ) -> dict:
        """Return current price + **24-hour sold volume** for an item.

        ``currency=1`` is USD. Full list:
        https://partner.steamgames.com/doc/store/pricing/currencies
        """
        raw = self.client.get_json(
            "/market/priceoverview/",
            params={
                "appid": appid,
                "currency": currency,
                "market_hash_name": market_hash_name,
            },
        )
        if not raw.get("success"):
            raise SteamError(f"priceoverview failed for {market_hash_name!r}")
        lowest = raw.get("lowest_price")
        median = raw.get("median_price")
        vol = raw.get("volume")
        return {
            "market_hash_name": market_hash_name,
            "appid": appid,
            "currency": currency,
            "lowest_price_text": lowest,
            "lowest_price": _parse_price(lowest),
            "median_price_text": median,
            "median_price": _parse_price(median),
            "volume_24h_text": vol,
            "volume_24h": _parse_int(vol),
            "fetched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }

    def price_overview_bulk(
        self,
        names: list[str],
        appid: int = CS2_APPID,
        currency: int = 1,
    ) -> list[dict]:
        """Loop ``price_overview`` over a list; each call is throttled."""
        out: list[dict] = []
        for name in names:
            try:
                out.append(self.price_overview(name, appid=appid, currency=currency))
            except SteamError as exc:
                out.append({"market_hash_name": name, "error": str(exc)})
        return out

    def search(
        self,
        query: str,
        appid: int = CS2_APPID,
        count: int = 20,
        start: int = 0,
    ) -> dict:
        """Search the market by text. Returns item names + current listings."""
        raw = self.client.get_json(
            "/market/search/render/",
            params={
                "query": query,
                "appid": appid,
                "norender": 1,
                "count": min(count, 100),
                "start": max(start, 0),
            },
        )
        items = []
        for row in raw.get("results", []) or []:
            items.append(
                {
                    "name": row.get("name"),
                    "hash_name": row.get("hash_name"),
                    "sell_listings": row.get("sell_listings"),
                    "sell_price_text": row.get("sell_price_text"),
                    "sell_price": _parse_price(row.get("sell_price_text")),
                    "app_name": row.get("app_name"),
                    "app_icon": row.get("app_icon"),
                }
            )
        return {
            "query": query,
            "appid": appid,
            "total_count": raw.get("total_count"),
            "start": start,
            "count": count,
            "items": items,
        }

    def price_history(self, market_hash_name: str, appid: int = CS2_APPID) -> dict:
        """Full per-sale history for an item.

        Requires ``STEAM_LOGIN_SECURE`` cookie. Returns a list of
        ``(timestamp, price, volume)`` tuples, where ``timestamp`` is the
        ISO date of the rollup (usually hourly for recent data, daily for
        older data).
        """
        if not self.client.login_secure_cookie:
            raise SteamError(
                "pricehistory requires STEAM_LOGIN_SECURE cookie — set "
                "STEAM_LOGIN_SECURE in .env"
            )
        raw = self.client.get_json(
            "/market/pricehistory/",
            params={"appid": appid, "market_hash_name": market_hash_name},
        )
        if not raw.get("success"):
            raise SteamError(f"pricehistory failed for {market_hash_name!r}")
        points = []
        for row in raw.get("prices", []) or []:
            if len(row) < 3:
                continue
            try:
                ts = datetime.strptime(row[0][:20], "%b %d %Y %H: +0")
                points.append(
                    {
                        "timestamp": ts.isoformat() + "Z",
                        "price": float(row[1]),
                        "volume": int(row[2]),
                    }
                )
            except (ValueError, TypeError):
                continue
        return {
            "market_hash_name": market_hash_name,
            "appid": appid,
            "price_prefix": raw.get("price_prefix"),
            "price_suffix": raw.get("price_suffix"),
            "points": points,
        }

    def item_url(self, market_hash_name: str, appid: int = CS2_APPID) -> str:
        """Convenience: canonical market listing URL."""
        return f"https://steamcommunity.com/market/listings/{appid}/{quote(market_hash_name)}"
