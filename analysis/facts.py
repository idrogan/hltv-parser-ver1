"""Load financial facts as a tidy DataFrame: one row per (period_end, metric).

Sources: Supabase (SUPABASE_URL / SUPABASE_SERVICE_ROLE) or a local JSON file
in the parser's normalized fact format (the /registries/.../accounts output).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd


def from_supabase(legal_entity_id: int) -> pd.DataFrame:
    url, key = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_SERVICE_ROLE"]
    q = urllib.parse.urlencode({
        "select": "period_end,metric,value,currency,segment,reliability,notes",
        "legal_entity_id": f"eq.{legal_entity_id}",
        "segment": "eq.",
        "order": "period_end",
    })
    req = urllib.request.Request(f"{url}/rest/v1/financial_fact?{q}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return _tidy(pd.DataFrame(json.loads(resp.read())))


def from_file(path: Path | str) -> pd.DataFrame:
    data = json.loads(Path(path).read_text())
    rows = data.get("facts", data) if isinstance(data, dict) else data
    return _tidy(pd.DataFrame(rows))


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["year"] = df["period_end"].str[:4].astype(int)
    df["value"] = pd.to_numeric(df["value"])
    return df.sort_values(["year", "metric"]).reset_index(drop=True)


def wide(df: pd.DataFrame) -> pd.DataFrame:
    """year x metric table (values in native currency)."""
    return df.pivot_table(index="year", columns="metric", values="value", aggfunc="last")
