"""Single-tournament hero card.

Reads ``tournaments`` (by local id) + ``matches`` joined to find the
unique teams participating in this tournament. Lays out a hero-style
card with: tournament name, status pill, dates + location subtitle, a
big prize-pool number, and a wrapped list of participating teams.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path

import httpx

from . import brand

log = logging.getLogger(__name__)


def _fetch_tournament(t_id: int) -> dict:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = os.environ["SUPABASE_SERVICE_ROLE"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = httpx.get(
        f"{base}/tournaments",
        params={
            "select": "id,source,source_id,name,tier,start_date,end_date,prize_pool_usd,location,status,raw_payload",
            "id": f"eq.{t_id}",
            "limit": 1,
        },
        headers=headers, timeout=30.0,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError(f"No tournament with id={t_id}")
    return rows[0]


def _fetch_teams(t_id: int) -> list[str]:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = os.environ["SUPABASE_SERVICE_ROLE"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = httpx.get(
        f"{base}/matches",
        params={
            "select": "team_a,team_b",
            "tournament_id": f"eq.{t_id}",
            "limit": 200,
        },
        headers=headers, timeout=30.0,
    )
    r.raise_for_status()
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in r.json():
        for t in (m.get("team_a"), m.get("team_b")):
            if t and t not in seen_set:
                seen.append(t)
                seen_set.add(t)
    return seen


def _fmt_money(amount):
    if amount is None:
        return "TBA"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}k"
    return f"${amount:.0f}"


def _fmt_dates(s: str | None, e: str | None) -> str:
    if not s and not e:
        return "dates TBA"
    if s and e:
        try:
            sd = datetime.strptime(s, "%Y-%m-%d").date()
            ed = datetime.strptime(e, "%Y-%m-%d").date()
            if sd.year == ed.year and sd.month == ed.month:
                return f"{sd.strftime('%b %-d')}–{ed.strftime('%-d, %Y')}"
            if sd.year == ed.year:
                return f"{sd.strftime('%b %-d')} – {ed.strftime('%b %-d, %Y')}"
            return f"{sd.strftime('%b %-d, %Y')} – {ed.strftime('%b %-d, %Y')}"
        except ValueError:
            pass
    return s or e or ""


def _status_color(status: str | None) -> tuple[str, str]:
    s = (status or "").lower()
    if s == "ongoing":
        return brand.ACCENT, "LIVE"
    if s == "upcoming":
        return brand.ACCENT_2, "UPCOMING"
    if s == "finished":
        return brand.TEXT_MUTED, "FINISHED"
    return brand.TEXT_MUTED, (status or "—").upper()


def render(args, out_dir: Path) -> list[Path]:
    t = _fetch_tournament(args.id)
    teams = _fetch_teams(args.id)

    name = t["name"]
    dates_str = _fmt_dates(t.get("start_date"), t.get("end_date"))
    location = t.get("location") or ""
    prize = _fmt_money(t.get("prize_pool_usd"))
    tier = (t.get("tier") or "").upper() or "—"
    pill_color, pill_text = _status_color(t.get("status"))

    # Strip the noisy " — Playoffs"/"— Stage N" suffix from the title.
    from .prize_pool_ladder import _STAGE_SUFFIXES
    display_name = name
    for suf in _STAGE_SUFFIXES:
        if display_name.endswith(suf):
            display_name = display_name[: -len(suf)]
            break

    paths: list[Path] = []
    for size in brand.SIZES:
        from matplotlib import pyplot as plt
        brand.apply_style()
        fig = plt.figure(figsize=size.figsize, dpi=size.dpi)
        fig.patch.set_facecolor(brand.BG)
        is_square = size.name == "1x1"

        # Vertical bands (figure-relative). Designed so nothing overlaps:
        #   0.92  status pill / tier badge
        #   0.80  title
        #   0.66  dates + location subtitle
        #   0.55  PRIZE POOL label
        #   0.34  big prize number (text TOP)
        #   0.22  PARTICIPATING TEAMS label
        #   0.16  teams strip (text TOP)
        #   0.04  footer

        # Status pill (top-right)
        fig.text(
            0.96, 0.92, pill_text,
            fontsize=brand.FONT_SIZE_SUB, color=brand.BG, fontweight="bold",
            ha="right", va="center",
            bbox=dict(boxstyle="round,pad=0.55", facecolor=pill_color, edgecolor="none"),
        )
        # Tier badge (top-left)
        fig.text(
            0.05, 0.92, f"TIER  {tier}",
            fontsize=brand.FONT_SIZE_FOOTER, color=brand.TEXT_MUTED,
            ha="left", va="center", fontweight="bold",
        )

        # Title
        fig.text(
            0.05, 0.80, display_name,
            fontsize=brand.FONT_SIZE_TITLE + (2 if not is_square else 0),
            color=brand.TEXT, fontweight="bold",
            ha="left", va="top", wrap=True,
        )

        # Dates + location subtitle
        sub = dates_str + (f"   ·   {location}" if location else "")
        fig.text(
            0.05, 0.66, sub,
            fontsize=brand.FONT_SIZE_SUB + 2, color=brand.TEXT_MUTED,
            ha="left", va="top",
        )

        # Prize pool label + big number
        fig.text(
            0.05, 0.55, "PRIZE POOL",
            fontsize=brand.FONT_SIZE_FOOTER, color=brand.TEXT_MUTED,
            ha="left", va="top", fontweight="bold",
        )
        fig.text(
            0.05, 0.50, prize,
            fontsize=72 if not is_square else 60,
            color=brand.ACCENT_3, fontweight="bold",
            ha="left", va="top",
        )

        # Teams band
        teams_label = (
            f"PARTICIPATING TEAMS ({len(teams)})" if teams else "TEAMS — TBA"
        )
        fig.text(
            0.05, 0.22, teams_label,
            fontsize=brand.FONT_SIZE_FOOTER, color=brand.TEXT_MUTED,
            ha="left", va="top", fontweight="bold",
        )
        if teams:
            cap = 16 if not is_square else 12
            shown = teams[:cap]
            extra = len(teams) - len(shown)
            line = "  ·  ".join(shown)
            if extra > 0:
                line += f"  +{extra} more"
            fig.text(
                0.05, 0.16, line,
                fontsize=brand.FONT_SIZE_BAR, color=brand.TEXT,
                ha="left", va="top", wrap=True,
            )

        brand.add_footer(fig)
        paths.append(brand.save(fig, "tournament-card", out_dir, size))
    return paths
