"""Top-N CS2 tournaments by prize pool — horizontal bar chart.

Reads ``tournaments`` from Supabase. Filters to a single calendar year
by start_date when --year is given. Sorts by prize_pool_usd descending,
takes top --limit, draws horizontal bars with the prize formatted in
the chart and a small dates / location annotation.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from . import brand

log = logging.getLogger(__name__)


def _fetch(year: int | None, limit: int, source: str | None) -> list[dict]:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = os.environ["SUPABASE_SERVICE_ROLE"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    params: dict[str, Any] = {
        "select": "id,name,tier,start_date,end_date,prize_pool_usd,location,source",
        "order": "prize_pool_usd.desc.nullslast",
        "limit": limit,
        "prize_pool_usd": "not.is.null",
    }
    if source:
        params["source"] = f"eq.{source}"
    if year is not None:
        params["start_date"] = f"gte.{year}-01-01"
        params["and"] = f"(start_date.lte.{year}-12-31)"

    r = httpx.get(f"{base}/tournaments", params=params, headers=headers, timeout=30.0)
    r.raise_for_status()
    return r.json()


_STAGE_SUFFIXES = (
    " — Playoffs", " — Stage 1", " — Stage 2", " — Stage 3",
    " — Group Stage", " — Group A", " — Group B", " — Main Event",
    " — Finals", " — Closed Qualifier", " — Open Qualifier",
)


def _short_name(name: str, max_len: int = 32) -> str:
    # Drop the noisy "— Playoffs" / "— Stage N" suffix that PandaScore
    # tacks onto every stage row.
    for suf in _STAGE_SUFFIXES:
        if name.endswith(suf):
            name = name[: -len(suf)]
            break
    if len(name) <= max_len:
        return name
    return name[: max_len - 1] + "…"


def _fmt_money(amount: float | None) -> str:
    if not amount:
        return "—"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}k"
    return f"${amount:.0f}"


def render(args, out_dir: Path) -> list[Path]:
    rows = _fetch(year=args.year, limit=args.limit, source=args.from_source)
    if not rows:
        raise RuntimeError(
            f"No tournaments matched (year={args.year}, limit={args.limit}, "
            f"source={args.from_source}). Try widening the filter."
        )

    # Reverse for matplotlib barh (it draws bottom-up).
    rows = list(reversed(rows))
    names = [_short_name(r["name"]) for r in rows]
    pools = [float(r["prize_pool_usd"]) for r in rows]
    money_labels = [_fmt_money(p) for p in pools]

    title = f"CS2 — top {args.limit} tournaments by prize pool"
    subtitle = (
        f"{args.year}"
        if args.year is not None
        else "all years currently in Supabase"
    )
    if args.from_source:
        subtitle += f"  ·  source={args.from_source}"

    paths: list[Path] = []
    for size in brand.SIZES:
        # Override the global axes box to give long y-labels the room
        # they need (~30% of the figure width).
        from matplotlib import pyplot as plt
        brand.apply_style()
        fig = plt.figure(figsize=size.figsize, dpi=size.dpi)
        ax = fig.add_axes([
            0.32,
            brand.MARGIN_BOTTOM,
            brand.MARGIN_RIGHT - 0.32,
            brand.MARGIN_TOP - brand.MARGIN_BOTTOM,
        ])
        bars = ax.barh(names, pools, color=brand.ACCENT, edgecolor="none", height=0.7)

        # Color the highest bar with the gold accent so it pops.
        bars[-1].set_color(brand.ACCENT_3)

        # Money labels at the end of each bar.
        x_max = max(pools) * 1.18
        for i, (bar, label) in enumerate(zip(bars, money_labels)):
            ax.text(
                bar.get_width() + max(pools) * 0.012,
                bar.get_y() + bar.get_height() / 2,
                label,
                va="center", ha="left",
                color=brand.TEXT,
                fontsize=brand.FONT_SIZE_BAR,
                fontweight="bold" if i == len(bars) - 1 else "normal",
            )

        ax.set_xlim(0, x_max)
        ax.tick_params(axis="x", which="both", bottom=False, labelbottom=False)
        ax.tick_params(axis="y", which="both", left=False, pad=8)
        ax.grid(axis="x", visible=False)

        for label in ax.get_yticklabels():
            label.set_color(brand.TEXT)
            label.set_fontsize(brand.FONT_SIZE_BAR)

        brand.add_title(fig, title, subtitle)
        brand.add_footer(fig)

        paths.append(brand.save(fig, "prize-pool-ladder", out_dir, size))
    return paths
