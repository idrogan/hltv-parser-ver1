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

    print("\n== densest table: first rows anatomy ==")
    tables = t.css("table")
    if tables:
        dense = max(tables, key=lambda tb: len(tb.css("tr")))
        rows = dense.css("tr")
        print(f"  (table rows={len(rows)})")
        for ri, tr in enumerate(rows[:3]):
            print(f"  --- row[{ri}] ---")
            for a in tr.css("a"):
                href = a.attributes.get("href") or ""
                txt = a.text(strip=True)
                if href or txt:
                    print(f"    a href={href[:60]!r} text={txt[:40]!r}")
            for td_i, td in enumerate(tr.css("td, th")):
                print(f"    td[{td_i}]: {td.text(strip=True)[:45]!r}")
                for el in td.css("[class]"):
                    own = el.text(deep=False, strip=True)
                    if own and len(own) <= 40:
                        cls = (el.attributes.get("class") or "")[:34]
                        print(f"        <{el.tag} .{cls}> {own!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
