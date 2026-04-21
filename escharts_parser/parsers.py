"""HTML parsers for escharts.com.

The site renders tournament rankings as tables with human-formatted
viewer counts (``1.2M``, ``345K``). We keep both the original string
and a parsed integer for each metric so downstream consumers can pick
whichever they need.

Selectors are intentionally forgiving — if EsportsCharts ships a
layout tweak, individual fields degrade to ``None`` rather than
crashing.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

BASE_URL = "https://escharts.com"
VIEWER_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*([KMB])?", re.IGNORECASE)
HOURS_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*(hrs?|h|hours)", re.IGNORECASE)


def _text(node: Optional[Node]) -> Optional[str]:
    if node is None:
        return None
    t = node.text(strip=True)
    return t or None


def _abs(href: Optional[str]) -> Optional[str]:
    return urljoin(BASE_URL, href) if href else None


def _parse_compact_number(text: Optional[str]) -> Optional[int]:
    """'1.2M' -> 1_200_000, '345K' -> 345_000, '12,345' -> 12345."""
    if not text:
        return None
    t = text.replace(",", "").strip()
    m = VIEWER_RE.search(t)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    suffix = (m.group(2) or "").upper()
    return int(num * mult.get(suffix, 1))


def _parse_hours(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = HOURS_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def parse_tournament_list(html: str) -> list[dict]:
    """Parse the tournament ranking table at ``/tournaments/<game>``."""
    tree = HTMLParser(html)
    rows: list[dict] = []

    table_rows = tree.css("table tbody tr")
    if not table_rows:
        table_rows = tree.css(".tournaments-table tr, .ranking-row, .chart-row")

    for tr in table_rows:
        link = tr.css_first("a[href*='/tournaments/']")
        if not link:
            continue
        href = link.attributes.get("href")
        name = _text(link)
        cells = tr.css("td")
        cell_texts = [_text(c) or "" for c in cells]

        peak = _pick_metric(tr, cells, keywords=("peak",))
        avg = _pick_metric(tr, cells, keywords=("avg", "average"))
        hours = _pick_metric(tr, cells, keywords=("hours watched", "watch time"))
        airtime = _pick_metric(tr, cells, keywords=("airtime", "air time", "broadcast"))

        rows.append(
            {
                "tournament": name,
                "url": _abs(href),
                "slug": (href or "").rstrip("/").split("/")[-1] or None,
                "peak_viewers_text": peak,
                "peak_viewers": _parse_compact_number(peak),
                "avg_viewers_text": avg,
                "avg_viewers": _parse_compact_number(avg),
                "hours_watched_text": hours,
                "hours_watched": _parse_compact_number(hours),
                "airtime_text": airtime,
                "airtime_hours": _parse_hours(airtime),
                "raw_cells": cell_texts,
            }
        )
    return rows


def _pick_metric(tr: Node, cells: list[Node], keywords: tuple) -> Optional[str]:
    """Try to extract a metric either by data attribute or by column order."""
    for key in keywords:
        data = tr.css_first(f"[data-metric*='{key}']")
        if data:
            t = _text(data)
            if t:
                return t
    for cell in cells:
        title = (cell.attributes.get("title") or "").lower()
        if any(k in title for k in keywords):
            return _text(cell)
    return None


def parse_tournament_detail(html: str) -> dict:
    """Parse a single tournament page at ``/tournaments/<game>/<slug>``."""
    tree = HTMLParser(html)

    name = _text(tree.css_first("h1"))

    stats: dict = {"tournament": name}
    for card in tree.css(
        ".stats-card, .stat-block, .tournament-stats .stat, .overview-stat"
    ):
        label = _text(card.css_first(".label, .title, .stat-label, .name"))
        value = _text(card.css_first(".value, .number, .stat-value"))
        if not label:
            continue
        key = label.lower()
        if "peak" in key:
            stats["peak_viewers_text"] = value
            stats["peak_viewers"] = _parse_compact_number(value)
        elif "avg" in key or "average" in key:
            stats["avg_viewers_text"] = value
            stats["avg_viewers"] = _parse_compact_number(value)
        elif "hours watched" in key or "watch time" in key:
            stats["hours_watched_text"] = value
            stats["hours_watched"] = _parse_compact_number(value)
        elif "airtime" in key or "broadcast" in key:
            stats["airtime_text"] = value
            stats["airtime_hours"] = _parse_hours(value)
        elif "prize" in key:
            stats["prize_pool_text"] = value
            stats["prize_pool"] = _parse_compact_number(value)

    channels: list[dict] = []
    for row in tree.css(".channel-row, .channels-table tbody tr"):
        ch_name = _text(row.css_first(".channel-name, td.name, .name"))
        ch_peak = _text(row.css_first(".peak, [data-metric*='peak']"))
        ch_hours = _text(row.css_first(".hours, [data-metric*='hours']"))
        if ch_name:
            channels.append(
                {
                    "channel": ch_name,
                    "peak_viewers_text": ch_peak,
                    "peak_viewers": _parse_compact_number(ch_peak),
                    "hours_watched_text": ch_hours,
                    "hours_watched": _parse_compact_number(ch_hours),
                }
            )
    if channels:
        stats["channels"] = channels

    return stats
