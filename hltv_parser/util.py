"""Shared parsing helpers."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import urlparse

from dateutil.relativedelta import relativedelta

PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
ID_FROM_PATH_RE = re.compile(r"/(\d+)/")


def text_or_none(node) -> Optional[str]:
    if node is None:
        return None
    txt = node.text(strip=True)
    return txt or None


def parse_percent(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    m = PERCENT_RE.search(value)
    return float(m.group(1)) if m else None


def parse_number(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    m = NUMBER_RE.search(value.replace(",", ""))
    return float(m.group(0)) if m else None


def parse_int(value: Optional[str]) -> Optional[int]:
    n = parse_number(value)
    return int(n) if n is not None else None


def extract_id_from_url(url: Optional[str]) -> Optional[int]:
    if not url:
        return None
    m = ID_FROM_PATH_RE.search(url)
    return int(m.group(1)) if m else None


def slug_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[-1] if parts else None


def normalise_date_range(
    start: Optional[str | date] = None,
    end: Optional[str | date] = None,
    months_back: Optional[int] = None,
) -> Tuple[str, str]:
    """Return ``(start, end)`` as ``YYYY-MM-DD`` strings.

    If ``months_back`` is given and ``start`` is missing, computes
    ``end - months_back months``. ``end`` defaults to today (UTC).
    """
    today = datetime.now(timezone.utc).date()
    end_d = _to_date(end) or today
    if start:
        start_d = _to_date(start)
    elif months_back:
        start_d = end_d - relativedelta(months=months_back)
    else:
        start_d = end_d - timedelta(days=90)
    return start_d.isoformat(), end_d.isoformat()


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    return datetime.strptime(v, "%Y-%m-%d").date()
