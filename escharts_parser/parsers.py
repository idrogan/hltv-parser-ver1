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
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})")


def _text(node: Optional[Node]) -> Optional[str]:
    if node is None:
        return None
    t = node.text(strip=True)
    return t or None


def _abs(href: Optional[str]) -> Optional[str]:
    return urljoin(BASE_URL, href) if href else None


def _parse_compact_number(text: Optional[str]) -> Optional[int]:
    """'1.2M' -> 1_200_000, '345K' -> 345_000, '208 874HW' -> 208_874.

    EsportsCharts groups thousands with spaces (often non-breaking) and
    glues unit tags onto the figure (``HW`` hours-watched, ``PV`` peak
    viewers), so strip whitespace/commas before reading the number.
    """
    if not text:
        return None
    t = re.sub(r"[\s, ]", "", text)
    m = re.search(r"(\d+(?:\.\d+)?)\s*([KMB])?", t, re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1))
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(num * mult.get((m.group(2) or "").upper(), 1))


def _parse_airtime_hours(text: Optional[str]) -> Optional[float]:
    """'11h 10m' -> 11.17, '86h' -> 86.0."""
    if not text:
        return None
    h = re.search(r"(\d+)\s*h", text)
    mi = re.search(r"(\d+)\s*m", text)
    if not h and not mi:
        return None
    return round((int(h.group(1)) if h else 0) + (int(mi.group(1)) if mi else 0) / 60, 2)


def _parse_date_range(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'27.05.26 - 30.05.26' -> ('2026-05-27', '2026-05-30')."""
    if not text:
        return (None, None)
    found = _DATE_RE.findall(text)
    iso = lambda d: f"20{d[2]}-{d[1]}-{d[0]}"
    if not found:
        return (None, None)
    return (iso(found[0]), iso(found[1]) if len(found) > 1 else None)


def _detail_anchors(tr: Node) -> list[Node]:
    """Row anchors that point at a tournament detail page (game/slug)."""
    out = []
    for a in tr.css("a[href*='/tournaments/']"):
        href = a.attributes.get("href") or ""
        if re.search(r"/tournaments/[^/]+/[^/?]+", href):
            out.append(a)
    return out


def parse_tournament_list(html: str) -> list[dict]:
    """Parse the tournament ranking table at ``/tournaments/<game>``.

    Columns (no per-row headers, classified by content): name+meta,
    prize ``$``, hours-watched ``…HW``, peak-viewers ``…PV``, airtime
    ``Nh Nm``, and the date range.
    """
    tree = HTMLParser(html)
    rows: list[dict] = []

    table_rows = tree.css("table tbody tr") or tree.css("table tr")
    for tr in table_rows:
        anchors = _detail_anchors(tr)
        if not anchors:
            continue
        href = anchors[0].attributes.get("href")
        # The logo link carries no text; the name lives on a sibling link.
        name = next((t for t in (_text(a) for a in anchors) if t), None)

        cell_texts = [_text(c) or "" for c in tr.css("td")]
        if name is None:
            name = _name_from_blob(cell_texts[0] if cell_texts else None)

        peak = next((c for c in cell_texts if c.rstrip().endswith("PV")), None)
        hours = next((c for c in cell_texts if c.rstrip().endswith("HW")), None)
        prize = next((c for c in cell_texts if c.lstrip().startswith("$")), None)
        airtime = next(
            (c for c in cell_texts if re.search(r"\d+\s*h(\s*\d+\s*m)?\s*$", c.strip())),
            None,
        )
        dates = next((c for c in cell_texts if _DATE_RE.search(c)), None)
        start, end = _parse_date_range(dates)

        rows.append(
            {
                "tournament": name,
                "url": _abs(href),
                "slug": (href or "").rstrip("/").split("/")[-1] or None,
                "peak_viewers_text": peak,
                "peak_viewers": _parse_compact_number(peak),
                "hours_watched_text": hours,
                "hours_watched": _parse_compact_number(hours),
                "airtime_text": airtime,
                "airtime_hours": _parse_airtime_hours(airtime),
                "prize_pool_text": prize,
                "prize_pool": _parse_compact_number(prize),
                "start_date": start,
                "end_date": end,
            }
        )
    return rows


def _name_from_blob(blob: Optional[str]) -> Optional[str]:
    """Last-resort name recovery from the glued first cell.

    The cell reads like ``LIVEOngoing<Name>OngoingCS2,<tags><dates><prize>``;
    the name is the run before the game tag, minus status words.
    """
    if not blob:
        return None
    head = re.split(r"CS2,|CS:GO,|CS2\b", blob, maxsplit=1)[0]
    for word in ("LIVE", "Ongoing", "Finished", "Upcoming"):
        head = head.replace(word, " ")
    name = " ".join(head.split())
    return name or None


def parse_tournament_detail(html: str) -> dict:
    """Parse a single tournament page at ``/tournaments/<game>/<slug>``."""
    tree = HTMLParser(html)

    name = _text(tree.css_first("h1"))
    if name:
        # h1 reads "IEM Atlanta 2026/ Statistics" — drop the page-section suffix.
        name = re.sub(r"\s*/\s*Statistics\s*$", "", name).strip() or None

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
            stats["airtime_hours"] = _parse_airtime_hours(value)
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
