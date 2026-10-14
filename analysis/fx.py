"""ECB annual-average reference rates (units of currency per 1 EUR).

Fetched from the ECB Data Portal API and cached in analysis/data/fx_cache.json,
so charts can be rebuilt offline. Series: EXR.A.<CCY>.EUR.SP00.A
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from pathlib import Path

CACHE = Path(__file__).parent / "data" / "fx_cache.json"
API = "https://data-api.ecb.europa.eu/service/data/EXR/A.{ccy}.EUR.SP00.A?format=csvdata"
SOURCE = "ECB euro reference rates, annual average"


def _load_cache() -> dict:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def annual_rates(ccy: str, refresh: bool = False) -> dict[int, float]:
    """{year: units of `ccy` per EUR}. EUR itself -> 1.0 for any year."""
    ccy = ccy.upper()
    if ccy == "EUR":
        return {}
    cache = _load_cache()
    if ccy in cache and not refresh:
        return {int(k): v for k, v in cache[ccy].items()}
    req = urllib.request.Request(API.format(ccy=ccy), headers={"Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = list(csv.DictReader(io.StringIO(resp.read().decode())))
    rates = {int(r["TIME_PERIOD"]): float(r["OBS_VALUE"]) for r in rows if r.get("OBS_VALUE")}
    cache[ccy] = rates
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))
    return rates


def to_eur(value: float, ccy: str | None, year: int, rates: dict[int, float] | None = None) -> float | None:
    if value is None or ccy is None:
        return value
    if ccy.upper() == "EUR":
        return value
    rates = rates if rates is not None else annual_rates(ccy)
    if year not in rates:
        return None
    return value / rates[year]
