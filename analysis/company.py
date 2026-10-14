"""Standard company pack: four charts + a CSV, from financial facts.

    python -m analysis.company --entity-id 12 --name "PGL" --out out/pgl            # from Supabase
    python -m analysis.company --file facts.json --name "PGL" --out out/pgl --native  # local, native currency

Charts (one measure per chart, never a dual axis):
  1. revenue by year            2. net result by year
  3. net margin %               4. revenue per employee
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from . import chart_style as cs
from . import facts as F
from . import fx


def build_table(df: pd.DataFrame, to_eur: bool = True) -> tuple[pd.DataFrame, str, str]:
    """Return (year table, money unit symbol, fx note)."""
    table = F.wide(df).astype(float)
    currency = df.loc[df["currency"].notna(), "currency"].mode().iat[0]
    unit, note = currency + " ", ""
    if to_eur and currency != "EUR":
        try:
            rates = fx.annual_rates(currency)
        except Exception as exc:  # offline / ECB down -> keep native currency, say so
            rates, note = {}, f"EUR conversion unavailable ({exc.__class__.__name__}); values in {currency}"
        if rates and all(y in rates for y in table.index):
            money = [c for c in table.columns if c != "employees"]
            for y in table.index:
                table.loc[y, money] = table.loc[y, money] / rates[y]
            unit, note = "€", f"{currency} converted at {fx.SOURCE}"
    if "revenue" in table and "net_result" in table:
        table["net_margin_pct"] = table["net_result"] / table["revenue"] * 100
    if "revenue" in table and "employees" in table:
        table["revenue_per_employee"] = table["revenue"] / table["employees"]
    return table, unit, note


def _columns(ax, years, values, color, unit, label_years):
    ax.bar([str(y) for y in years], values, width=0.5, color=color, zorder=2)
    ax.axhline(0, color=cs.INK["axis"], linewidth=1, zorder=3)
    for y, v in zip(years, values):
        if y in label_years and pd.notna(v):
            ax.annotate(cs.compact(v, unit), (str(y), v), xytext=(0, 6 if v >= 0 else -16),
                        textcoords="offset points", ha="center", fontsize=12.5,
                        color=cs.INK["primary"], fontweight="semibold")
    ax.yaxis.set_major_formatter(lambda v, _: cs.compact(v) if v else "0")   # unit lives in the subtitle


def _line(ax, years, values, color, fmt, label_years):
    xs = [str(y) for y in years]
    ax.plot(xs, values, color=color, zorder=3)
    ax.scatter(xs, values, s=64, color=color, edgecolor=cs.SURFACE, linewidth=2, zorder=4)
    vals = list(values)
    for i, (y, v) in enumerate(zip(years, vals)):
        if y in label_years and pd.notna(v):
            # put the label on the side the line is NOT coming from, so it never sits on the stroke
            prev = vals[i - 1] if i > 0 else None
            below = prev is not None and pd.notna(prev) and prev > v
            ax.annotate(fmt(v), (str(y), v), xytext=(0, -20 if below else 10), textcoords="offset points",
                        ha="center",
                        fontsize=12.5, color=cs.INK["primary"], fontweight="semibold")
    ax.yaxis.set_major_formatter(lambda v, _: fmt(v))


def _highlights(series: pd.Series) -> set[int]:
    s = series.dropna()
    if s.empty:
        return set()
    return {int(s.index.min()), int(s.index.max()), int(s.idxmax()), int(s.idxmin())}


def render(table: pd.DataFrame, name: str, unit: str, out_dir: Path, source: str, note: str = "") -> list[Path]:
    years = [int(y) for y in table.index]
    span = f"{years[0]}–{years[-1]}"
    money = "EUR" if unit == "€" else unit.strip()
    files: list[Path] = []

    if "revenue" in table:
        fig, ax = cs.new_figure(f"{name}: revenue by year", f"Net turnover, {money}, {span}")
        _columns(ax, years, table["revenue"], cs.SERIES[0], unit, _highlights(table["revenue"]))
        files.append(cs.finish(fig, source, out_dir / "01_revenue.png", note))

    if "net_result" in table:
        fig, ax = cs.new_figure(f"{name}: net result by year", f"Profit (loss) after tax, {money}, {span}")
        _columns(ax, years, table["net_result"], cs.SERIES[0], unit, _highlights(table["net_result"]))
        files.append(cs.finish(fig, source, out_dir / "02_net_result.png", note))

    if "net_margin_pct" in table:
        fig, ax = cs.new_figure(f"{name}: net margin", f"Net result as % of revenue, {span}")
        _line(ax, years, table["net_margin_pct"], cs.SERIES[0], lambda v: f"{v:.0f}%",
              _highlights(table["net_margin_pct"]))
        ax.set_ylim(bottom=min(0, table["net_margin_pct"].min() * 1.2))
        files.append(cs.finish(fig, source, out_dir / "03_net_margin.png", note))

    if "revenue_per_employee" in table:
        fig, ax = cs.new_figure(f"{name}: revenue per employee", f"Revenue ÷ average headcount, {money}, {span}")
        _line(ax, years, table["revenue_per_employee"], cs.SERIES[0], lambda v: cs.compact(v),
              _highlights(table["revenue_per_employee"]))
        ax.set_ylim(bottom=0)
        files.append(cs.finish(fig, source, out_dir / "04_revenue_per_employee.png", note))

    csv_path = out_dir / "table.csv"
    table.round(2).to_csv(csv_path)
    files.append(csv_path)
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--entity-id", type=int, help="legal_entity.id in Supabase")
    src.add_argument("--file", help="JSON in the /registries/.../accounts format")
    ap.add_argument("--name", required=True)
    ap.add_argument("--source", default="official registry filings")
    ap.add_argument("--out", required=True)
    ap.add_argument("--native", action="store_true", help="keep native currency (no EUR conversion)")
    args = ap.parse_args(argv)

    df = F.from_supabase(args.entity_id) if args.entity_id else F.from_file(args.file)
    table, unit, note = build_table(df, to_eur=not args.native)
    for p in render(table, args.name, unit, Path(args.out), args.source, note):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
