"""Debug helper: dump the DOM structure of a saved HLTV maps page.

Used to fix stale selectors in hltv_parser/parsers.py after a CloakBrowser
fetch shows the WAF is pierced but some fields parse as null. Run against
an HTML file produced by HLTV_CLOAK_DUMP_DIR.

    python scripts/hltv_inspect.py out/hltv_html/stats_teams_maps_4914_3dmax.html

Prints compact, paste-friendly signal:
  1. <title> + byte size (sanity)
  2. every class attribute containing 'map' (counts) — tells the real
     per-map stat container apart from the top map-pool filter widget
  3. all .stats-row key:value pairs found anywhere — reveals whether
     CT/T side data lives in stats-rows and under which key
  4. short text nodes mentioning CT / T side + their parent class —
     finds where the side win% actually sits in the current DOM
  5. .standard-box highlight pairs (label -> value) exactly as the
     overview parser reads them — shows why aggregate stats parse null
  6. raw .small-label-below / .large-strong text — catches the case
     where the label/value are not nested the way the parser expects
  7. .stats-sub-map-grid text — the per-map breakdown that actually
     ships on the overview page
"""
from __future__ import annotations

import sys
from collections import Counter

from selectolax.parser import HTMLParser


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/hltv_inspect.py <dumped.html>", file=sys.stderr)
        return 2
    html = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    t = HTMLParser(html)

    title = t.css_first("title")
    print(f"title: {title.text(strip=True) if title else None!r}")
    print(f"bytes: {len(html)}")

    print("\n== class attrs containing 'map' (count) ==")
    c: Counter[str] = Counter()
    for n in t.css("[class]"):
        for cls in (n.attributes.get("class") or "").split():
            if "map" in cls.lower():
                c[cls] += 1
    for cls, n in c.most_common(40):
        print(f"  {n:4}  .{cls}")

    print("\n== .stats-row key : value (all over page) ==")
    seen = set()
    for row in t.css(".stats-row"):
        sp = row.css("span")
        if len(sp) >= 2:
            k = sp[0].text(strip=True)
            v = sp[-1].text(strip=True)
            sig = (k, v)
            if sig in seen:
                continue
            seen.add(sig)
            print(f"  {k!r} : {v!r}")

    print("\n== short text nodes mentioning CT / T-side + parent class ==")
    hits = 0
    for n in t.css("*"):
        if n.child is not None:   # only leaf-ish text nodes
            continue
        txt = n.text(strip=True)
        if not txt or len(txt) > 18:
            continue
        low = txt.lower()
        if "ct" in low or low in ("t", "t side", "terrorist") or "%" in txt:
            par = n.parent
            pcls = par.attributes.get("class") if par else None
            print(f"  <{n.tag} class={n.attributes.get('class')}> {txt!r}  ^parent .{pcls}")
            hits += 1
            if hits >= 60:
                print("  ...(truncated at 60)")
                break

    print("\n== .standard-box highlight pairs (label -> value) ==")
    for box in t.css(".standard-box .col, .standard-box .columns .col"):
        label = box.css_first(".small-label-below")
        value = box.css_first(".large-strong")
        if label or value:
            lt = label.text(strip=True) if label else None
            vt = value.text(strip=True) if value else None
            print(f"  {lt!r} -> {vt!r}")

    print("\n== raw .small-label-below / .large-strong text ==")
    for sel in (".small-label-below", ".large-strong"):
        vals = [n.text(strip=True) for n in t.css(sel)]
        vals = [v for v in vals if v][:20]
        print(f"  {sel}: {vals}")

    print("\n== .stats-sub-map-grid text ==")
    grid = t.css_first(".stats-sub-map-grid")
    if grid is not None:
        for cell in grid.css("[class]"):
            txt = cell.text(strip=True)
            if txt and len(txt) <= 40:
                print(f"  .{cell.attributes.get('class')}: {txt!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
