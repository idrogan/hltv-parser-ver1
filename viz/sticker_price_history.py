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


def _panel_capsule(event_slug: str, buckets, out_dir: Path,
                   tier_filter: str, file_suffix: str) -> Path | None:
    """One capsule panel restricted to a single placement-tier bucket.

    ``tier_filter`` ∈ {'top3', 'rest'}. Each panel shows three lines
    (paper / holo / foil-or-glitter) for that single tier, so the
    high-value Foil line doesn't compress paper into the x-axis.

    Title is short ('Atlanta 2017 · Top-3 capsule stickers'). Legend
    sits below the plot area to keep the chart itself uncluttered.
    """
    from matplotlib import pyplot as plt
    brand.apply_style()

    is_atlanta = "atlanta" in event_slug
    team_categories = ["paper", "holo", "foil" if is_atlanta else "glitter"]

    fig = plt.figure(figsize=brand.WIDESCREEN.figsize, dpi=brand.WIDESCREEN.dpi)
    # Custom margins: leave 14% at the bottom for the legend + footer.
    ax = fig.add_axes([0.08, 0.18, 0.88, 0.62])

    plotted = False
    for cat in team_categories:
        b = buckets.get((cat, tier_filter))
        if not b:
            continue
        monthly = _resample_monthly(b.daily_median)
        xs = list(monthly.keys())
        ys = list(monthly.values())
        color = _CATEGORY_COLOR[cat]
        ax.plot(
            xs, ys,
            color=color, linewidth=2.2,
            marker="o", markersize=3.5, markerfacecolor=color,
            markeredgecolor=color,
            label=f"{_CATEGORY_LABEL[cat]}  (n={b.item_count})",
        )
        plotted = True

    if not plotted:
        log.warning("no capsule buckets for %s/%s — panel skipped",
                    event_slug, tier_filter)
        return None

    _add_event_guides(ax, event_slug)
    _format_time_axis(ax)
    ax.set_ylabel("USD · monthly median", color=brand.TEXT_MUTED)
    ax.grid(True, which="major", alpha=0.35)
    ax.grid(True, which="minor", alpha=0.12)

    # Legend below the chart (in the 12% bottom band, above the footer).
    ax.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.08),
        frameon=False, labelcolor=brand.TEXT,
        ncols=len(team_categories), fontsize=brand.FONT_SIZE_BAR,
    )

    meta = _EVENT_META[event_slug]
    short_event = meta["name"].replace("ELEAGUE Major: ", "") \
                              .replace("PGL Major: ", "")
    tier_label = "Top-3 teams" if tier_filter == "top3" else "Rest of field"
    brand.add_title(
        fig,
        f"{short_event} · {tier_label}",
        f"capsule stickers · monthly median · "
        f"release {meta['start'].isoformat()}",
    )
    brand.add_footer(fig)
    return brand.save(fig, f"{event_slug}-capsule-{file_suffix}",
                      out_dir, brand.WIDESCREEN)


def _panel_autographs(event_slug: str, buckets, out_dir: Path) -> Path | None:
    """Autograph categories shown across the top tier (Top3 + Finalist).

    Each category becomes one line (paper / holo / foil-or-glitter /
    gold). Champion-tier autographs live on the next panel because of
    the price-scale gap.
    """
    from matplotlib import pyplot as plt
    brand.apply_style()

    fig = plt.figure(figsize=brand.WIDESCREEN.figsize, dpi=brand.WIDESCREEN.dpi)
    ax = fig.add_axes([0.08, 0.18, 0.88, 0.62])

    is_atlanta = "atlanta" in event_slug
    autograph_categories = (
        ["paper", "holo", "foil", "gold"] if is_atlanta
        else ["paper", "holo", "glitter", "gold"]
    )

    plotted = False
    for cat in autograph_categories:
        # Merge top3 + finalist series — they're the "non-champion
        # podium" buckets and have similar item counts.
        merged: dict = {}
        item_total = 0
        for tier in ("top3", "finalist"):
            b = buckets.get((cat, tier))
            if not b:
                continue
            item_total += b.item_count
            for d, v in b.daily_median.items():
                merged.setdefault(d, []).append(v)
        if not merged:
            continue
        # Median across the joined points per date, then monthly resample.
        from statistics import median
        daily = {d: median(vs) for d, vs in merged.items()}
        monthly = _resample_monthly(daily)
        if not monthly:
            continue
        xs = list(monthly.keys())
        ys = list(monthly.values())
        color = _CATEGORY_COLOR[cat]
        ax.plot(
            xs, ys, color=color, linewidth=2.2,
            marker="o", markersize=3.5, markerfacecolor=color,
            markeredgecolor=color,
            label=f"{_CATEGORY_LABEL[cat]}  (n={item_total})",
        )
        plotted = True

    if not plotted:
        log.warning("no autograph buckets for %s — panel skipped", event_slug)
        return None

    _add_event_guides(ax, event_slug)
    _format_time_axis(ax)
    ax.set_ylabel("USD · monthly median", color=brand.TEXT_MUTED)
    ax.grid(True, which="major", alpha=0.35)
    ax.grid(True, which="minor", alpha=0.12)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08),
              frameon=False, labelcolor=brand.TEXT,
              ncols=len(autograph_categories), fontsize=brand.FONT_SIZE_BAR)

    meta = _EVENT_META[event_slug]
    short_event = meta["name"].replace("ELEAGUE Major: ", "") \
                              .replace("PGL Major: ", "")
    brand.add_title(
        fig,
        f"{short_event} · Finalist + Top-3 autographs",
        "player autographs · monthly median across both tiers",
    )
    brand.add_footer(fig)
    return brand.save(fig, f"{event_slug}-autographs", out_dir, brand.WIDESCREEN)


def _panel_champion(event_slug: str, buckets, out_dir: Path) -> Path | None:
    """Champion-tier autographs only. Categories are the lines.

    Y is log-scaled because a $200 Champion-Gold sits two decades
    above the $2 Champion-Paper on the same timeline.
    """
    from matplotlib import pyplot as plt
    brand.apply_style()

    fig = plt.figure(figsize=brand.WIDESCREEN.figsize, dpi=brand.WIDESCREEN.dpi)
    ax = fig.add_axes([0.08, 0.18, 0.88, 0.62])

    is_atlanta = "atlanta" in event_slug
    champ_categories = (
        ["paper", "holo", "foil", "gold"] if is_atlanta
        else ["paper", "holo", "glitter", "gold", "champion_gold"]
    )

    plotted = False
    for cat in champ_categories:
        b = buckets.get((cat, "champion"))
        if not b:
            continue
        monthly = _resample_monthly(b.daily_median)
        # Strip zeros for log scale.
        monthly = {d: v for d, v in monthly.items() if v > 0}
        if not monthly:
            continue
        xs = list(monthly.keys())
        ys = list(monthly.values())
        color = _CATEGORY_COLOR.get(cat, brand.ACCENT)
        ax.plot(
            xs, ys, color=color, linewidth=2.2,
            marker="o", markersize=3.5, markerfacecolor=color,
            markeredgecolor=color,
            label=f"{_CATEGORY_LABEL.get(cat, cat)}  (n={b.item_count})",
        )
        plotted = True

    if not plotted:
        log.warning("no champion buckets for %s — panel skipped", event_slug)
        return None

    ax.set_yscale("log")
    _add_event_guides(ax, event_slug)
    _format_time_axis(ax)
    ax.set_ylabel("USD · monthly median (log scale)", color=brand.TEXT_MUTED)
    ax.grid(True, which="major", alpha=0.35)
    ax.grid(True, which="minor", alpha=0.12)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08),
              frameon=False, labelcolor=brand.TEXT,
              ncols=len(champ_categories), fontsize=brand.FONT_SIZE_BAR)

    meta = _EVENT_META[event_slug]
    short_event = meta["name"].replace("ELEAGUE Major: ", "") \
                              .replace("PGL Major: ", "")
    champ_team = "Astralis" if is_atlanta else "NaVi"
    brand.add_title(
        fig,
        f"{short_event} · Champion autographs",
        f"{champ_team} player autographs · monthly median · log scale",
    )
    brand.add_footer(fig)
    return brand.save(fig, f"{event_slug}-champion-autographs",
                      out_dir, brand.WIDESCREEN)


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

    # Capsule: two panels (top3 vs rest) so the foil/glitter line
    # doesn't crush the paper/holo lines into the x-axis.
    for tier, suffix in (("top3", "stickers-top3"), ("rest", "stickers-rest")):
        try:
            p = _panel_capsule(event_slug, buckets, out_dir, tier, suffix)
            if p is not None:
                paths.append(p)
        except Exception as exc:
            log.error("panel capsule/%s failed: %s", tier, exc)

    for fn in (_panel_autographs, _panel_champion, _panel_roi_summary):
        try:
            p = fn(event_slug, buckets, out_dir)
            if p is not None:
                paths.append(p)
        except Exception as exc:
            log.error("panel %s failed: %s", fn.__name__, exc)
    return paths
