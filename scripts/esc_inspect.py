"""Debug helper: dump the DOM structure of a saved EsportsCharts page.

escharts.com renders tournament rankings/detail pages whose markup the
parsers in escharts_parser/parsers.py no longer match (empty results).
Run against an HTML file produced by ESCHARTS_CLOAK_DUMP_DIR to see the
real structure and fix the selectors.

    python scripts/esc_inspect.py out/esc_html/tournaments_cs2.html

Prints compact, paste-friendly signal:
  1. <title> + byte size (sanity / challenge check)
  2. most common class tokens — finds the repeating row/card container
  3. <table> shapes (rows x cols) if any
  4. anchors to /tournaments/ (href + text) — the ranking rows
  5. short text nodes mentioning Peak/Avg/Hours/Airtime + parent class
  6. headings (h1/h2/h3)
"""
from __future__ import annotations

import re
import sys
from collections import Counter

from selectolax.parser import HTMLParser


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/esc_inspect.py <dumped.html>", file=sys.stderr)
        return 2
    html = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    t = HTMLParser(html)

    title = t.css_first("title")
    print(f"title: {title.text(strip=True) if title else None!r}")
    print(f"bytes: {len(html)}")

    print("\n== most common class tokens (top 40) ==")
    c: Counter[str] = Counter()
    for n in t.css("[class]"):
        for cls in (n.attributes.get("class") or "").split():
            c[cls] += 1
    for cls, n in c.most_common(40):
        print(f"  {n:5}  .{cls}")

    print("\n== <table> shapes ==")
    for i, table in enumerate(t.css("table")):
        rows = table.css("tr")
        first_cols = len(rows[0].css("td, th")) if rows else 0
        cls = table.attributes.get("class")
        print(f"  table[{i}] class={cls!r} rows={len(rows)} cols~={first_cols}")

    print("\n== anchors to /tournaments/ (href -> text) [first 40] ==")
    seen = set()
    n_a = 0
    for a in t.css("a[href*='/tournaments/']"):
        href = a.attributes.get("href") or ""
        if href in seen:
            continue
        seen.add(href)
        par = a.parent
        pcls = par.attributes.get("class") if par else None
        print(f"  {href}  -> {a.text(strip=True)[:50]!r}  ^parent .{pcls}")
        n_a += 1
        if n_a >= 40:
            print("  ...(truncated at 40)")
            break

    print("\n== own-text nodes mentioning Peak/Avg/Hours/Airtime/Prize ==")
    kws = ("peak", "average", "avg", "hours", "watch", "airtime", "air time", "prize")
    hits = 0
    for n in t.css("[class]"):
        txt = n.text(deep=False, strip=True)
        if not txt or len(txt) > 40:
            continue
        low = txt.lower()
        if any(k in low for k in kws):
            par = n.parent
            pcls = par.attributes.get("class") if par else None
            print(f"  <{n.tag} class={n.attributes.get('class')}> {txt!r}  ^parent .{pcls}")
            hits += 1
            if hits >= 50:
                print("  ...(truncated at 50)")
                break

    print("\n== headings ==")
    for sel in ("h1", "h2", "h3"):
        for n in t.css(sel)[:8]:
            txt = n.text(strip=True)
            if txt:
                print(f"  <{sel} class={n.attributes.get('class')}> {txt[:60]!r}")

    print("\n== all tables: class + first rows ==")
    for ti, table in enumerate(t.css("table")):
        rows = table.css("tr")
        cls = table.attributes.get("class")
        print(f"  table[{ti}] class={cls!r} rows={len(rows)}")
        for ri, tr in enumerate(rows[:3]):
            cells = [c.text(strip=True)[:30] for c in tr.css("td, th")]
            print(f"    row[{ri}]: {cells}")

    print("\n== viewer-like numeric own-text nodes (class ^parent) ==")
    num_re = re.compile(r"^\$?\s*\d[\d\s.,]*\s*[KMBkmb]?$")
    seen = 0
    for n in t.css("[class]"):
        own = n.text(deep=False, strip=True)
        if not own or len(own) > 16:
            continue
        if num_re.match(own) and sum(ch.isdigit() for ch in own) >= 3:
            par = n.parent
            pcls = par.attributes.get("class") if par else None
            print(f"  <{n.tag} .{n.attributes.get('class')}> {own!r}  ^parent .{pcls}")
            seen += 1
            if seen >= 60:
                print("  ...(truncated at 60)")
                break

    print("\n== headline stat containers (value + sibling label) ==")
    shown = 0
    for n in t.css("[class]"):
        cls = n.attributes.get("class") or ""
        if not ("text-default" in cls and "text-right" in cls and "font-bold" in cls):
            continue
        own = n.text(deep=False, strip=True)
        if not (own and num_re.match(own) and sum(ch.isdigit() for ch in own) >= 3):
            continue
        box = n.parent.parent if (n.parent and n.parent.parent) else n.parent
        if box is None:
            continue
        texts = []
        for el in box.css("[class]"):
            ot = el.text(deep=False, strip=True)
            if ot and len(ot) <= 40:
                texts.append(ot)
        print(f"  value={own!r} -> box own-texts: {texts[:10]}")
        shown += 1
        if shown >= 8:
            break

    print("\n== 'Overall statistics' block (own-text of container descendants) ==")
    target = None
    for n in t.css("h1, h2, h3, div, span"):
        if (n.text(strip=True) or "").lower().startswith("overall statistics"):
            target = n
            break
    if target is not None:
        container = target.parent.parent if (target.parent and target.parent.parent) else target.parent
        if container is not None:
            for el in container.css("[class]"):
                own = el.text(deep=False, strip=True)
                if own and len(own) <= 40:
                    cls = (el.attributes.get("class") or "")[:34]
                    print(f"  <{el.tag} .{cls}> {own!r}")
    else:
        print("  (no 'Overall statistics' heading found)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
