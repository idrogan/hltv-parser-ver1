"""Sticker price history charts — four panels per event.

  1. ``{event}-capsule-stickers.png``     paper / holo / premium team stickers
  2. ``{event}-autographs.png``           regular gold autographs per team
  3. ``{event}-champion-autographs.png``  champion tier only, log scale
  4. ``{event}-roi-summary.png``          ROI grid: categories × buyer profiles

Each panel reuses ``viz/brand.py`` so palette / typography stay
consistent. Per spec:

  * Line color encodes the category (Paper / Holo / Foil / Glitter / Gold).
  * Line style encodes the team-placement tier (top3 = solid,
    rest = dashed). Champion / finalist live on their own panel so
    they don't share the legend.
  * A vertical reference line marks the event date — the post needs
    "you bought day-zero" to be visually obvious.
  * Champion autograph panel is log-scale because s1mple-(Champion)
    sits 100x above any rest-tier paper sticker — a linear panel
    would crush every other line into the bottom 1%.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from . import brand
from analytics.sticker_roi import (
    Bucket,
    aggregate,
    roi_table,
    BUYER_PROFILES,
)

log = logging.getLogger(__name__)


# Category-to-color mapping. Order also defines legend order in panel 1.
_CATEGORY_COLOR = {
    "paper":         brand.TEXT_MUTED,
    "holo":          brand.ACCENT_2,
    "foil":          brand.ACCENT,
    "glitter":       "#7c5cff",     # purple — Stockholm "Glitter"
    "gold":          brand.ACCENT_3,
    "champion_gold": brand.ACCENT,  # only used on the log-scale panel
}

_TIER_LINESTYLE = {
    "top3":     "-",
    "rest":     "--",
    "finalist": "-",
    "champion": "-",
}

# Pretty labels used in legends and panel subtitles.
_CATEGORY_LABEL = {
    "paper":         "Paper",
    "holo":          "Holo",
    "foil":          "Foil",
    "glitter":       "Glitter",
    "gold":          "Gold",
    "champion_gold": "Champion",
}

# Manually picked event dates (capsule release ≈ tournament start date).
_EVENT_META = {
    "eleague-atlanta-2017": {
        "name":  "ELEAGUE Major: Atlanta 2017",
        "start": date(2017, 1, 22),
    },
    "pgl-stockholm-2021": {
        "name":  "PGL Major: Stockholm 2021",
        "start": date(2021, 10, 26),
    },
}

# CS:GO → CS2 transition (Sept 27, 2023) — drawn as a subtle vertical guide.
_CS2_LAUNCH = date(2023, 9, 27)


def _resample_monthly(series: dict[date, float]) -> dict[date, float]:
    """Collapse a daily ``{date: price}`` map to one point per month.

    The point is anchored at the first of each month (matplotlib draws
    nice ticks at month boundaries), and the value is the median across
    every day in that month. Reduces a 3400-day Atlanta series down
    to ~110 monthly points — the chart can breathe.
    """
    from statistics import median
    by_month: dict[date, list[float]] = {}
    for d, v in series.items():
        anchor = date(d.year, d.month, 1)
        by_month.setdefault(anchor, []).append(v)
    return {m: round(median(vs), 4) for m, vs in sorted(by_month.items())}


def _format_time_axis(ax) -> None:
    """Major ticks per year, minor per quarter, '2017'-style labels."""
    from matplotlib.dates import YearLocator, MonthLocator, DateFormatter
    ax.xaxis.set_major_locator(YearLocator())
    ax.xaxis.set_minor_locator(MonthLocator(bymonth=(4, 7, 10)))
    ax.xaxis.set_major_formatter(DateFormatter("%Y"))
    ax.tick_params(axis="x", which="major", labelsize=brand.FONT_SIZE_BAR,
                   colors=brand.TEXT)
    ax.tick_params(axis="x", which="minor", length=3)


def _panel_capsule(event_slug: str, buckets, out_dir: Path) -> Path:
    """Panel 1: paper + holo + premium TEAM stickers (top3 vs rest)."""
    from matplotlib import pyplot as plt
    brand.apply_style()

    is_atlanta = "atlanta" in event_slug
    team_categories = ["paper", "holo", "foil" if is_atlanta else "glitter"]

    fig, ax = brand.new_figure(brand.WIDESCREEN)

    plotted = False
    for cat in team_categories:
        color = _CATEGORY_COLOR[cat]
        for tier in ("top3", "rest"):
            b = buckets.get((cat, tier))
            if not b:
                continue
            monthly = _resample_monthly(b.daily_median)
            xs = list(monthly.keys())
            ys = list(monthly.values())
            ax.plot(
                xs, ys,
                color=color, linewidth=2.2,
                linestyle=_TIER_LINESTYLE[tier],
                marker="o", markersize=3.5, markerfacecolor=color,
                markeredgecolor=color, alpha=0.95,
                label=f"{_CATEGORY_LABEL[cat]} · {tier}",
            )
            plotted = True

    if not plotted:
        raise RuntimeError(
            f"no buckets to plot for capsule panel ({event_slug})"
        )

    _add_event_guides(ax, event_slug)
    _format_time_axis(ax)
    ax.set_ylabel("USD · monthly median across bucket",
                  color=brand.TEXT_MUTED)
    ax.grid(True, which="major", alpha=0.35)
    ax.grid(True, which="minor", alpha=0.12)
    ax.legend(loc="upper left", frameon=False, labelcolor=brand.TEXT, ncols=3,
              fontsize=brand.FONT_SIZE_BAR)
    meta = _EVENT_META[event_slug]
    span_days = (max(b.latest_date for b in buckets.values())
                 - meta["start"]).days
    brand.add_title(
        fig,
        f"Sticker prices · {meta['name']} · Capsule stickers",
        f"release: {meta['start'].isoformat()} · {span_days} days of history "
        f"· solid = top3 teams · dashed = rest",
    )
    brand.add_footer(fig)
    return brand.save(fig, f"{event_slug}-capsule-stickers", out_dir, brand.WIDESCREEN)


def _panel_autographs(event_slug: str, buckets, out_dir: Path) -> Path:
    """Panel 2: regular high-tier player autographs across team tiers.

    Different events use different "premium autograph" categories:
      * Atlanta 2017 — ``foil`` (Gold variant exists but never traded)
      * Stockholm 2021 — ``gold``
    We pick the one whose bucket has the most items.
    """
    from matplotlib import pyplot as plt
    brand.apply_style()
    fig, ax = brand.new_figure(brand.WIDESCREEN)

    # Pick whichever autograph category has the richest data across
    # podium+rest tiers. Atlanta lacks gold autograph trades, so it
    # falls back to foil naturally.
    autograph_candidates = ("gold", "foil", "champion_gold")
    cat_choice = max(
        autograph_candidates,
        key=lambda c: sum(
            buckets[(c, t)].item_count
            for t in ("champion", "finalist", "top3", "rest")
            if (c, t) in buckets
        ),
        default=None,
    )
    if not cat_choice or not any(
        (cat_choice, t) in buckets for t in ("champion", "finalist", "top3", "rest")
    ):
        log.warning("no autograph buckets for %s — panel skipped", event_slug)
        return None  # type: ignore[return-value]

    color = _CATEGORY_COLOR.get(cat_choice, brand.ACCENT_3)
    plotted = False
    for tier, label, style in (
        ("champion", "Champion (winners)",  "-"),
        ("finalist", "Finalist (runners-up)", "-"),
        ("top3",     "Top3 (semifinalists)",  "-"),
        ("rest",     "Rest of field",          "--"),
    ):
        b = buckets.get((cat_choice, tier))
        if not b:
            continue
        xs = sorted(b.daily_median.keys())
        ys = [b.daily_median[x] for x in xs]
        # Tint each tier slightly via alpha so the four lines are
        # distinguishable while staying inside the category's color.
        alpha = 1.0 if tier in ("champion", "finalist") else 0.7
        ax.plot(xs, ys, color=color, linewidth=2.0,
                linestyle=style, alpha=alpha, label=label)
        plotted = True

    if not plotted:
        log.warning("no autograph buckets for %s — panel skipped", event_slug)
        return None  # type: ignore[return-value]

    _add_event_guides(ax, event_slug)
    ax.set_ylabel("USD (bucket median)", color=brand.TEXT_MUTED)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", frameon=False, labelcolor=brand.TEXT,
              fontsize=brand.FONT_SIZE_BAR)
    meta = _EVENT_META[event_slug]
    brand.add_title(
        fig,
        f"Sticker prices · {meta['name']} · "
        f"{_CATEGORY_LABEL.get(cat_choice, cat_choice)} autographs",
        "median per placement tier",
    )
    brand.add_footer(fig)
    return brand.save(fig, f"{event_slug}-autographs", out_dir, brand.WIDESCREEN)


def _panel_champion(event_slug: str, buckets, out_dir: Path) -> Path:
    """Panel 3: champion-tier autographs on log scale."""
    from matplotlib import pyplot as plt
    brand.apply_style()
    fig, ax = brand.new_figure(brand.WIDESCREEN)

    # Atlanta 2017 has no separate Champion category — the top-tier
    # autograph is (Foil) or (Gold). For Atlanta we plot the 'gold'
    # category restricted to placement_tier='champion'. For Stockholm
    # we plot the 'champion_gold' category.
    is_atlanta = "atlanta" in event_slug
    if is_atlanta:
        champ_keys = [("foil", "champion"), ("gold", "champion")]
        tier_label = "Astralis player Foil & Gold autographs"
    else:
        champ_keys = [("champion_gold", "champion"), ("gold", "champion")]
        tier_label = "NaVi player Champion & Gold autographs"

    any_plotted = False
    for cat, tier in champ_keys:
        b = buckets.get((cat, tier))
        if not b:
            continue
        xs = sorted(b.daily_median.keys())
        ys = [b.daily_median[x] for x in xs if b.daily_median[x] > 0]
        xs = [x for x, y in zip(xs, [b.daily_median[x] for x in xs]) if y > 0]
        ax.plot(xs, ys,
                color=_CATEGORY_COLOR.get(cat, brand.ACCENT),
                linewidth=2.0,
                label=_CATEGORY_LABEL.get(cat, cat))
        any_plotted = True

    if not any_plotted:
        log.warning("no champion buckets for %s — panel skipped", event_slug)
        return None  # type: ignore[return-value]

    ax.set_yscale("log")
    _add_event_guides(ax, event_slug)
    ax.set_ylabel("USD (log scale)", color=brand.TEXT_MUTED)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="upper right", frameon=False, labelcolor=brand.TEXT,
              fontsize=brand.FONT_SIZE_BAR)
    meta = _EVENT_META[event_slug]
    brand.add_title(fig,
                    f"Sticker prices · {meta['name']} · Champion autographs",
                    f"{tier_label}  ·  log scale")
    brand.add_footer(fig)
    return brand.save(fig, f"{event_slug}-champion-autographs", out_dir, brand.WIDESCREEN)


def _panel_roi_summary(event_slug: str, buckets, out_dir: Path) -> Path:
    """Panel 4: heat-map-style grid of ROI per category × buyer profile."""
    from matplotlib import pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    brand.apply_style()
    fig = plt.figure(figsize=brand.WIDESCREEN.figsize, dpi=brand.WIDESCREEN.dpi)

    rows = roi_table(buckets)
    if not rows:
        raise RuntimeError(f"roi_table is empty for {event_slug}")

    # Pivot: dict[(category, tier)][profile] = roi_pct
    cells: dict[tuple[str, str], dict[str, dict]] = {}
    for r in rows:
        cells.setdefault((r["category"], r["placement_tier"]), {})[r["profile"]] = r

    # Display order: stable, only the buckets we actually have.
    cat_order = ["paper", "holo", "foil", "glitter", "gold", "champion_gold"]
    tier_order = ["champion", "finalist", "top3", "rest"]
    profiles = list(BUYER_PROFILES.keys())

    row_labels = []
    cell_values: list[list[float | None]] = []
    cell_texts:  list[list[str]] = []
    for cat in cat_order:
        for tier in tier_order:
            key = (cat, tier)
            if key not in cells:
                continue
            label = f"{_CATEGORY_LABEL.get(cat, cat)} · {tier}"
            row_labels.append(label)
            row_v: list[float | None] = []
            row_t: list[str] = []
            for prof in profiles:
                d = cells[key].get(prof)
                if d is None:
                    row_v.append(None); row_t.append("—")
                else:
                    row_v.append(d["roi_pct"])
                    sign = "+" if d["roi_pct"] >= 0 else ""
                    row_t.append(f"{sign}{d['roi_pct']:.0f}%\n${d['today_price_usd']:.2f}")
            cell_values.append(row_v)
            cell_texts.append(row_t)

    n_rows = len(row_labels)
    n_cols = len(profiles)

    ax = fig.add_axes([0.22, 0.10, 0.72, 0.74])
    ax.set_xticks(range(n_cols))
    ax.set_xticklabels([p.upper() for p in profiles],
                       color=brand.TEXT, fontsize=brand.FONT_SIZE_BAR)
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(row_labels, color=brand.TEXT,
                       fontsize=brand.FONT_SIZE_BAR)
    ax.invert_yaxis()
    ax.set_xticks([x - 0.5 for x in range(1, n_cols)], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, n_rows)], minor=True)
    ax.grid(which="minor", color=brand.BG, linewidth=2)
    ax.tick_params(which="both", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    cmap = LinearSegmentedColormap.from_list(
        "roi", [(0, "#7a1a1a"), (0.5, "#30363d"), (1, "#1f6f3a")],
        N=128,
    )
    # Symmetric color scale around 0, capped at +/- 1000% so a 50,000%
    # outlier doesn't flatten the rest of the grid.
    vmax = min(1000, max(abs(v) for row in cell_values for v in row if v is not None) or 1)
    for i, row in enumerate(cell_values):
        for j, v in enumerate(row):
            if v is None:
                color = brand.SURFACE
            else:
                norm = max(-1, min(1, v / vmax))   # -1 .. 1
                color = cmap((norm + 1) / 2)
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1,
                                        facecolor=color, edgecolor="none"))
            ax.text(j, i, cell_texts[i][j], ha="center", va="center",
                    color=brand.TEXT, fontsize=brand.FONT_SIZE_BAR,
                    fontweight="bold")
    ax.set_xlim(-0.5, n_cols - 0.5)
    ax.set_ylim(n_rows - 0.5, -0.5)

    meta = _EVENT_META[event_slug]
    brand.add_title(fig,
                    f"Sticker ROI · {meta['name']}",
                    "median bucket price today vs buy-window median  ·  "
                    "rows = (category · placement tier), cols = buyer profile")
    brand.add_footer(fig)
    return brand.save(fig, f"{event_slug}-roi-summary", out_dir, brand.WIDESCREEN)


def _add_event_guides(ax, event_slug: str) -> None:
    """Vertical guide lines: event release + CS:GO→CS2 transition."""
    meta = _EVENT_META[event_slug]
    ax.axvline(meta["start"], color=brand.ACCENT_3, alpha=0.6, linewidth=1.2)
    ax.text(meta["start"], ax.get_ylim()[1] * 0.98, " event start",
            color=brand.ACCENT_3, fontsize=brand.FONT_SIZE_FOOTER,
            va="top", alpha=0.8)
    # Only mark the CS2 transition if the chart's date range crosses it.
    xlim = ax.get_xlim()
    from matplotlib.dates import date2num
    if xlim[0] <= date2num(_CS2_LAUNCH) <= xlim[1]:
        ax.axvline(_CS2_LAUNCH, color=brand.TEXT_MUTED, alpha=0.35,
                   linewidth=1.0, linestyle=":")
        ax.text(_CS2_LAUNCH, ax.get_ylim()[1] * 0.85, " CS2 launch",
                color=brand.TEXT_MUTED, fontsize=brand.FONT_SIZE_FOOTER,
                va="top", alpha=0.55)


def render(args, out_dir: Path) -> list[Path]:
    """CLI entry. ``args.event_slug`` selects the event; emits 4 PNGs."""
    event_slug = args.event_slug
    if event_slug not in _EVENT_META:
        raise ValueError(
            f"unknown event_slug={event_slug!r}; known: {list(_EVENT_META)}"
        )
    buckets = aggregate(event_slug)
    paths = []
    for fn in (_panel_capsule, _panel_autographs, _panel_champion, _panel_roi_summary):
        try:
            p = fn(event_slug, buckets, out_dir)
            if p is not None:
                paths.append(p)
        except Exception as exc:
            log.error("panel %s failed: %s", fn.__name__, exc)
    return paths
