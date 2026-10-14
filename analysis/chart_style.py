"""House chart style. Every chart goes through ``new_figure`` and ``finish`` so
posts, threads and the site look like one publication.

Palette and chrome follow the validated reference instance from the dataviz
method (categorical order fixed, ink tokens for text, hairline grid).
Rebrand by editing BRAND / SERIES / INK only.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

BRAND = "CS Money Desk"            # watermark text; change when the blog has a name
HANDLE = ""                        # e.g. "@yourhandle"

SURFACE = "#fcfcfb"
INK = {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
       "grid": "#e1e0d9", "axis": "#c3c2b7"}
# Fixed categorical order — never cycle, never reorder by rank.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GOOD, CRITICAL = "#006300", "#d03b3b"   # text tokens for signed deltas (always with a sign, never alone)

SIZES = {"x": (12, 6.75), "square": (8, 8), "story": (6.75, 12)}   # 16:9 posts, square, vertical shorts


def _font() -> str:
    names = {f.name for f in font_manager.fontManager.ttflist}
    for candidate in ("Inter", "Helvetica Neue", "Arial", "DejaVu Sans"):
        if candidate in names:
            return candidate
    return "sans-serif"


def apply() -> None:
    plt.rcParams.update({
        "font.family": _font(),
        "font.size": 13,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": INK["axis"],
        "axes.labelcolor": INK["secondary"],
        "axes.titlecolor": INK["primary"],
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": INK["grid"],
        "grid.linewidth": 1,
        "grid.linestyle": "-",
        "xtick.color": INK["muted"],
        "ytick.color": INK["muted"],
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "lines.linewidth": 2,
        "lines.solid_capstyle": "round",
        "lines.solid_joinstyle": "round",
        "savefig.facecolor": SURFACE,
        "legend.frameon": False,
    })


def new_figure(title: str, subtitle: str = "", size: str = "x"):
    apply()
    fig, ax = plt.subplots(figsize=SIZES[size], dpi=150)
    fig.subplots_adjust(left=0.09, right=0.96, top=0.80, bottom=0.16)
    fig.text(0.04, 0.93, title, fontsize=22, fontweight="semibold", color=INK["primary"], va="bottom")
    if subtitle:
        fig.text(0.04, 0.905, subtitle, fontsize=13.5, color=INK["secondary"], va="top")
    return fig, ax


def finish(fig, source: str, out: Path | str, note: str = "") -> Path:
    """Source line bottom-left (mandatory), brand bottom-right, save PNG."""
    line = f"Source: {source}" + (f"  ·  {note}" if note else "")
    fig.text(0.04, 0.035, line, fontsize=10.5, color=INK["muted"], va="bottom")
    mark = BRAND + (f"  {HANDLE}" if HANDLE else "")
    fig.text(0.96, 0.035, mark, fontsize=10.5, color=INK["secondary"], ha="right", va="bottom",
             fontweight="semibold")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def compact(value: float, unit: str = "") -> str:
    """1_234_567 -> '1.2M'. Unit prefix like '€'."""
    v = abs(value)
    sign = "−" if value < 0 else ""
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            num = v / div
            text = f"{num:.1f}" if num < 100 else f"{num:.0f}"
            return f"{sign}{unit}{text.removesuffix('.0')}{suf}"
    return f"{sign}{unit}{v:,.0f}"
