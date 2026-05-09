"""Shared brand config — palette, type, geometry, footer.

Every chart template imports from here so changing the palette in one
place updates all charts. Constants are deliberately Python-level
(not env vars) — design changes are commits, not config tweaks.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import matplotlib as mpl
import matplotlib.pyplot as plt


# ---- Sizes (in inches @ 100dpi → pixels) -----------------------------------

class Size(NamedTuple):
    name: str        # filename suffix
    figsize: tuple[float, float]  # inches
    dpi: int

# Twitter card 1.91:1, but the safer 16:9 = 1200x675 renders well
# across Twitter, Substack hero image, and LinkedIn.
WIDESCREEN = Size(name="16x9",    figsize=(12.00, 6.75), dpi=100)
SQUARE     = Size(name="1x1",     figsize=(10.80, 10.80), dpi=100)
SIZES = (WIDESCREEN, SQUARE)


# ---- Palette ---------------------------------------------------------------

# Dark-mode first; bright accents for the data so the chart reads on
# both X timeline (dark default) and a light Substack background after
# someone screenshots it.
BG          = "#0d1117"   # GitHub dark — neutral, near-black
SURFACE     = "#161b22"   # raised surface (card panels, footer)
GRID        = "#30363d"   # gridlines
TEXT        = "#e6edf3"   # primary text
TEXT_MUTED  = "#8b949e"   # secondary text / axis labels

ACCENT      = "#ff6b35"   # primary accent — orange/red, energetic
ACCENT_2    = "#4ec9b0"   # secondary accent — teal, calm contrast
ACCENT_3    = "#f7b801"   # gold — for prize / "premium" emphasis
ACCENT_DIM  = "#5a4030"   # dimmed accent for "not the main bar"

# Sequence used when many bars need distinct colors. Cycles politely.
SEQUENCE = [ACCENT, ACCENT_2, ACCENT_3, "#7c5cff", "#3fb950", "#f778ba"]


# ---- Typography ------------------------------------------------------------

# DejaVu Sans is what matplotlib ships with; it covers Cyrillic + Latin
# + most special characters out of the box. Switching to Inter / Roboto
# is a font-install change later, no code change.
FONT_FAMILY = "DejaVu Sans"
FONT_SIZE_TITLE  = 28
FONT_SIZE_SUB    = 16
FONT_SIZE_BAR    = 13
FONT_SIZE_AXIS   = 12
FONT_SIZE_FOOTER = 11


# ---- Layout ----------------------------------------------------------------

MARGIN_LEFT   = 0.07
MARGIN_RIGHT  = 0.96
MARGIN_TOP    = 0.86   # leaves room for title + subtitle
MARGIN_BOTTOM = 0.10   # leaves room for footer

FOOTER_TEXT = "Data: PandaScore + Steam Market  ·  hltv-parser-ver1"


# ---- Setup -----------------------------------------------------------------

def apply_style() -> None:
    """Set matplotlib rcParams to match the brand. Idempotent."""
    mpl.rcParams.update({
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "savefig.facecolor": BG,
        "axes.edgecolor": GRID,
        "axes.labelcolor": TEXT_MUTED,
        "axes.titlecolor": TEXT,
        "xtick.color": TEXT_MUTED,
        "ytick.color": TEXT_MUTED,
        "grid.color": GRID,
        "grid.linestyle": "--",
        "grid.alpha": 0.4,
        "text.color": TEXT,
        "font.family": FONT_FAMILY,
        "font.size": FONT_SIZE_AXIS,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.spines.bottom": False,
    })


def new_figure(size: Size) -> tuple[plt.Figure, plt.Axes]:
    apply_style()
    fig = plt.figure(figsize=size.figsize, dpi=size.dpi)
    ax = fig.add_axes([
        MARGIN_LEFT,
        MARGIN_BOTTOM,
        MARGIN_RIGHT - MARGIN_LEFT,
        MARGIN_TOP - MARGIN_BOTTOM,
    ])
    return fig, ax


def add_title(fig: plt.Figure, title: str, subtitle: str | None = None) -> None:
    fig.text(
        MARGIN_LEFT, 0.94, title,
        fontsize=FONT_SIZE_TITLE, fontweight="bold", color=TEXT,
        ha="left", va="top",
    )
    if subtitle:
        fig.text(
            MARGIN_LEFT, 0.89, subtitle,
            fontsize=FONT_SIZE_SUB, color=TEXT_MUTED,
            ha="left", va="top",
        )


def add_footer(fig: plt.Figure, custom: str | None = None) -> None:
    fig.text(
        MARGIN_LEFT, 0.04, custom or FOOTER_TEXT,
        fontsize=FONT_SIZE_FOOTER, color=TEXT_MUTED,
        ha="left", va="bottom",
    )


def save(fig: plt.Figure, base_name: str, out_dir: Path, size: Size) -> Path:
    """Save fig under ``out_dir/{base_name}-{size.name}-{ts}.png``."""
    from datetime import datetime, timezone
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{base_name}-{size.name}-{ts}.png"
    fig.savefig(path, dpi=size.dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path
