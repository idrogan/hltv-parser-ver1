"""Bar chart of current sticker prices for one event.

Reads ``watched_items`` for the given ``event_slug``, then for each
item pulls the most recent row from ``steam_prices``. Lays out side-by-
side groups: regular sticker (small bar) vs Holo (tall bar) for each
team, sorted by Holo price descending.

Naming heuristic: Steam Market sticker names follow
   "Sticker | <team> [(Holo)] | <event>"
We split on " | ", strip the " (Holo)" / " (Foil)" / " (Gold)"
modifier, and group by the bare team name.
"""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from pathlib import Path

import httpx

from . import brand

log = logging.getLogger(__name__)

_VARIANT_RE = re.compile(r"\s*\(([^)]+)\)\s*$")


def _split_team_variant(market_hash_name: str) -> tuple[str, str]:
    """'Sticker | Spirit (Holo) | Copenhagen 2024' →
       ('Spirit', 'Holo')
    'Sticker | FaZe Clan | Antwerp 2022' →
       ('FaZe Clan', 'Regular')
    """
    parts = [p.strip() for p in market_hash_name.split("|")]
    if len(parts) < 2:
        return market_hash_name, "Regular"
    team_with_variant = parts[1]
    m = _VARIANT_RE.search(team_with_variant)
    if m:
        team = team_with_variant[: m.start()].strip()
        variant = m.group(1).strip()
    else:
        team = team_with_variant
        variant = "Regular"
    # Normalise common Steam-side prefixes.
    team = team.replace("Team ", "")
    return team, variant


def _fetch_event_items(event_slug: str) -> list[dict]:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = os.environ["SUPABASE_SERVICE_ROLE"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = httpx.get(
        f"{base}/watched_items",
        params={"select": "market_hash_name,event_slug,added_at", "event_slug": f"eq.{event_slug}"},
        headers=headers, timeout=30.0,
    )
    r.raise_for_status()
    return r.json()


def _fetch_latest_price(market_hash_name: str) -> dict | None:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = os.environ["SUPABASE_SERVICE_ROLE"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = httpx.get(
        f"{base}/steam_prices",
        params={
            "select": "market_hash_name,lowest_price,median_price,volume_24h,fetched_at",
            "market_hash_name": f"eq.{market_hash_name}",
            "order": "fetched_at.desc",
            "limit": 1,
        },
        headers=headers, timeout=30.0,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def render(args, out_dir: Path) -> list[Path]:
    items = _fetch_event_items(args.event_slug)
    if not items:
        raise RuntimeError(
            f"No watched_items with event_slug={args.event_slug!r}. "
            "Seed first via `python cli.py steam seed --event-slug ...`."
        )

    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for it in items:
        team, variant = _split_team_variant(it["market_hash_name"])
        snap = _fetch_latest_price(it["market_hash_name"])
        if not snap:
            continue
        grouped[team][variant] = snap

    if not grouped:
        raise RuntimeError(
            f"No steam_prices rows yet for event_slug={args.event_slug!r}. "
            "Run `python cli.py steam refresh --event-slug ...` first."
        )

    # Sort teams by Holo price desc (fall back to regular).
    def _hilite(team: str) -> float:
        variants = grouped[team]
        for v in ("Holo", "Foil", "Gold", "Regular"):
            snap = variants.get(v)
            if snap and snap.get("lowest_price") is not None:
                return float(snap["lowest_price"])
        return 0.0

    teams = sorted(grouped.keys(), key=_hilite, reverse=True)[: args.top]

    paths: list[Path] = []
    for size in brand.SIZES:
        from matplotlib import pyplot as plt
        brand.apply_style()
        fig = plt.figure(figsize=size.figsize, dpi=size.dpi)
        bottom = 0.16  # leave headroom so x-labels don't collide with footer
        ax = fig.add_axes([
            0.10, bottom,
            brand.MARGIN_RIGHT - 0.10,
            brand.MARGIN_TOP - bottom,
        ])

        n = len(teams)
        x = list(range(n))
        bar_w = 0.38

        regular = [
            (grouped[t].get("Regular") or {}).get("lowest_price") or 0
            for t in teams
        ]
        holo = [
            (grouped[t].get("Holo") or {}).get("lowest_price") or 0
            for t in teams
        ]

        ax.bar([i - bar_w / 2 for i in x], regular, width=bar_w,
               color=brand.ACCENT_DIM, label="Regular", edgecolor="none")
        ax.bar([i + bar_w / 2 for i in x], holo, width=bar_w,
               color=brand.ACCENT, label="Holo", edgecolor="none")

        # Money labels on Holo bars (the headline number).
        for i, h in enumerate(holo):
            if h > 0:
                ax.text(
                    i + bar_w / 2, h, f"${h:.2f}",
                    ha="center", va="bottom",
                    color=brand.TEXT, fontsize=brand.FONT_SIZE_BAR,
                    fontweight="bold",
                )

        ax.set_xticks(x)
        ax.set_xticklabels(teams, rotation=0, color=brand.TEXT,
                           fontsize=brand.FONT_SIZE_BAR)
        ax.set_yticks([])
        ax.tick_params(axis="x", which="both", bottom=False, pad=8)
        for spine in ax.spines.values():
            spine.set_visible(False)

        # Legend manually placed top-right.
        from matplotlib.patches import Patch
        legend = ax.legend(
            handles=[
                Patch(facecolor=brand.ACCENT_DIM, label="Regular"),
                Patch(facecolor=brand.ACCENT, label="Holo"),
            ],
            loc="upper right",
            frameon=False,
            labelcolor=brand.TEXT,
            fontsize=brand.FONT_SIZE_BAR,
        )

        title = "Sticker prices  ·  Holo vs Regular"
        subtitle = f"event = {args.event_slug}  ·  data: Steam Market (latest snapshot)"
        brand.add_title(fig, title, subtitle)
        brand.add_footer(fig)

        paths.append(brand.save(fig, "sticker-prices", out_dir, size))
    return paths
