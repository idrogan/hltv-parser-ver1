"""Analysis layer: table building, FX conversion, chart files."""
from __future__ import annotations

import pandas as pd

from analysis import chart_style as cs
from analysis import company, fx


def _df():
    rows = []
    for year, rev, net, emp in ((2023, 140_000_000, 25_000_000, 50), (2024, 196_000_000, 50_000_000, 50)):
        rows += [
            {"period_end": f"{year}-12-31", "metric": "revenue", "value": rev, "currency": "RON"},
            {"period_end": f"{year}-12-31", "metric": "net_result", "value": net, "currency": "RON"},
            {"period_end": f"{year}-12-31", "metric": "employees", "value": emp, "currency": None},
        ]
    from analysis.facts import _tidy
    return _tidy(pd.DataFrame(rows))


def test_compact():
    assert cs.compact(196_429_575) == "196M"
    assert cs.compact(52_173_472, "€") == "€52.2M"
    assert cs.compact(50_000_000) == "50M"
    assert cs.compact(-1_500) == "−1.5K"


def test_native_table_has_margin_and_per_employee():
    table, unit, note = company.build_table(_df(), to_eur=False)
    assert unit == "RON " and note == ""
    assert round(table.loc[2024, "net_margin_pct"], 1) == 25.5
    assert table.loc[2024, "revenue_per_employee"] == 3_920_000


def test_eur_conversion_keeps_headcount(monkeypatch):
    monkeypatch.setattr(fx, "annual_rates", lambda ccy, refresh=False: {2023: 4.9, 2024: 5.0})
    table, unit, note = company.build_table(_df(), to_eur=True)
    assert unit == "€" and "ECB" in note
    assert table.loc[2024, "revenue"] == 196_000_000 / 5.0
    assert table.loc[2024, "employees"] == 50                     # never converted
    assert round(table.loc[2024, "net_margin_pct"], 1) == 25.5    # ratio unchanged by FX


def test_eur_conversion_falls_back_when_offline(monkeypatch):
    def boom(ccy, refresh=False):
        raise OSError("offline")
    monkeypatch.setattr(fx, "annual_rates", boom)
    table, unit, note = company.build_table(_df(), to_eur=True)
    assert unit == "RON " and "unavailable" in note


def test_render_writes_four_charts_and_csv(tmp_path):
    table, unit, _ = company.build_table(_df(), to_eur=False)
    files = company.render(table, "Test", unit, tmp_path, "unit test")
    assert sorted(p.name for p in files) == [
        "01_revenue.png", "02_net_result.png", "03_net_margin.png", "04_revenue_per_employee.png", "table.csv"]
    assert all(p.stat().st_size > 1000 for p in files if p.suffix == ".png")
