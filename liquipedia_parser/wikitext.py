"""Minimal wikitext parser for Liquipedia tournament pages.

Scope: just enough to read

  * ``{{Infobox league|...}}`` — tournament metadata (name, prize, dates,
    tier, location).
  * ``{{prize pool slot|...}}`` (a.k.a. ``{{Slot|...}}`` inside a prize
    pool table) — per-placement payout and participant.

We deliberately don't pull a real wikitext parser (``mwparserfromhell``)
to keep the dep tree small. Templates nest, so a flat ``\\{\\{...\\}\\}``
regex is wrong: we use a brace-counting tokenizer that respects nested
``{{ }}`` and pipes inside ``[[ ]]`` links and ``{| |}`` tables.

Returns plain dicts; the service layer reshapes them for DB writes.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

log = logging.getLogger(__name__)


# Strip wiki link decoration: ``[[Target|Display]]`` → ``Display``,
# ``[[Target]]`` → ``Target``.
_LINK_RE = re.compile(r"\[\[([^\]\|]+)(?:\|([^\]]+))?\]\]")

# HTML comments and stray tags we want to peel off display values.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Money strings like "$1,000,000", "1.25 M", "USD 500,000".
_MONEY_RE = re.compile(r"[-+]?\d[\d,\.]*")


def clean_value(s: str) -> str:
    """Reduce wikitext to a plain readable string.

    Order matters: strip comments first (they can contain pipes that
    would otherwise confuse a downstream consumer), then collapse
    links, then strip remaining HTML, then trim.
    """
    s = _HTML_COMMENT_RE.sub("", s)
    s = _LINK_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), s)
    s = _HTML_TAG_RE.sub("", s)
    return s.strip()


def parse_money_usd(value: str) -> float | None:
    """Best-effort USD parse. Returns ``None`` if no numeric token.

    Liquipedia stores prize pools in many shapes — ``$1,500,000``,
    ``1,500,000 USD``, ``USD 1.5M``. We grab the first numeric token,
    multiplicatively handle a trailing M/K, ignore currency if it
    isn't USD-shaped.
    """
    if not value:
        return None
    text = clean_value(value)
    m = _MONEY_RE.search(text)
    if not m:
        return None
    raw = m.group(0).replace(",", "")
    try:
        n = float(raw)
    except ValueError:
        return None
    # Trailing magnitude letter immediately after the number.
    tail = text[m.end():m.end() + 2].strip().upper()
    if tail.startswith("M"):
        n *= 1_000_000
    elif tail.startswith("K"):
        n *= 1_000
    return n


def _split_top_level_pipes(body: str) -> list[str]:
    """Split a template body on '|' that sits at brace/bracket depth 0.

    Respects nested ``{{ }}``, ``[[ ]]`` and ``{| |}``. Wikitext also
    nests ``<nowiki>`` etc.; we don't need that for our templates so
    we don't model it.
    """
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    depth_brace = 0   # {{ }}
    depth_link = 0    # [[ ]]
    depth_table = 0   # {| |}
    n = len(body)
    while i < n:
        c = body[i]
        two = body[i:i + 2]
        if two == "{{":
            depth_brace += 1
            buf.append(two)
            i += 2
            continue
        if two == "}}":
            depth_brace = max(0, depth_brace - 1)
            buf.append(two)
            i += 2
            continue
        if two == "[[":
            depth_link += 1
            buf.append(two)
            i += 2
            continue
        if two == "]]":
            depth_link = max(0, depth_link - 1)
            buf.append(two)
            i += 2
            continue
        if two == "{|":
            depth_table += 1
            buf.append(two)
            i += 2
            continue
        if two == "|}":
            depth_table = max(0, depth_table - 1)
            buf.append(two)
            i += 2
            continue
        if c == "|" and depth_brace == 0 and depth_link == 0 and depth_table == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def iter_templates(wikitext: str, name: str | None = None) -> Iterator[dict]:
    """Yield ``{template_name, params, raw}`` for every top-level template.

    If ``name`` is given, only yield templates whose name (case-folded,
    spaces/underscores collapsed) matches.

    ``params`` is a dict where named params are keyed by their name and
    positional ones by their 1-based index as a string ("1", "2", ...).
    """
    def _norm(n: str) -> str:
        return n.strip().replace("_", " ").lower()

    target = _norm(name) if name else None

    i = 0
    n = len(wikitext)
    while i < n:
        # Find next top-level "{{". We need to skip those that are
        # nested inside another already-opened template, but since
        # we're scanning at depth 0 only, this is straightforward: when
        # we find one, walk forward with a brace counter until it closes.
        start = wikitext.find("{{", i)
        if start < 0:
            return
        # Walk forward to find the matching "}}".
        depth = 1
        j = start + 2
        while j < n and depth > 0:
            two = wikitext[j:j + 2]
            if two == "{{":
                depth += 1
                j += 2
            elif two == "}}":
                depth -= 1
                j += 2
            else:
                j += 1
        if depth != 0:
            # Unbalanced — bail out rather than loop forever.
            log.warning("event=wikitext_unbalanced offset=%d", start)
            return

        raw = wikitext[start:j]
        body = wikitext[start + 2:j - 2]
        parts = _split_top_level_pipes(body)
        if not parts:
            i = j
            continue
        tpl_name = parts[0].strip()

        if target is None or _norm(tpl_name) == target:
            params: dict[str, str] = {}
            positional = 0
            for p in parts[1:]:
                if "=" in p:
                    # Split on the FIRST '=' only — values may contain '='.
                    key, _, val = p.partition("=")
                    params[key.strip()] = val.strip()
                else:
                    positional += 1
                    params[str(positional)] = p.strip()
            yield {"name": tpl_name, "params": params, "raw": raw}

        i = j


# ---- Higher-level extractors -----------------------------------------------

def extract_infobox_league(wikitext: str) -> dict | None:
    """Return the first ``{{Infobox league|...}}`` as a flat dict.

    Field names follow Liquipedia's CS infobox: ``name``, ``series``,
    ``tier``, ``liquipediatier``, ``liquipediatiertype``,
    ``organizer``, ``sponsor``, ``location``, ``city``, ``country``,
    ``venue``, ``format``, ``prizepool``, ``prizepoolusd``,
    ``sdate``, ``edate``, ``startdate``, ``enddate``, ``teams``,
    ``players``. We keep raw strings; callers decide how to coerce.
    """
    for tpl in iter_templates(wikitext, name="Infobox league"):
        return {k: clean_value(v) for k, v in tpl["params"].items()}
    return None


def extract_prize_pool_slots(wikitext: str) -> list[dict]:
    """Return per-placement rows from ``{{prize pool slot|...}}``.

    Liquipedia's prize pool table is built from sibling
    ``{{prize pool slot|place=1|usdprize=500000|...}}`` blocks. Place
    can be ``"1"``, ``"2-3"``, ``"4-8"`` etc. — we preserve the raw
    string. Participants live in ``team1``/``team2``/... positional or
    named keys depending on the template version, so we collect
    anything that smells like a team slot.

    Schema:
        {"place": "1", "usd": 500000.0, "participants": ["Astralis"]}
    """
    out: list[dict] = []
    for tpl in iter_templates(wikitext, name="prize pool slot"):
        p = tpl["params"]
        place = clean_value(p.get("place") or p.get("1") or "")
        if not place:
            continue
        usd_raw = p.get("usdprize") or p.get("usd") or p.get("prizemoney") or ""
        usd = parse_money_usd(usd_raw) if usd_raw else None
        participants: list[str] = []
        for key, val in p.items():
            if not val:
                continue
            kl = key.lower()
            if kl.startswith("team") or kl.startswith("player") or kl.startswith("opponent"):
                cleaned = clean_value(val)
                if cleaned:
                    participants.append(cleaned)
        out.append({
            "place": place,
            "usd": usd,
            "participants": participants,
        })
    return out
