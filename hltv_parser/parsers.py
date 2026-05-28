"""HTML -> dict parsers for HLTV pages.

Selectors are written defensively: if HLTV changes a layout, individual
fields fall back to ``None`` instead of raising. Each parser returns a
plain ``dict`` / ``list[dict]`` so the result can be serialised straight
to JSON for N8N or Make.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from .util import (
    extract_id_from_url,
    parse_int,
    parse_number,
    parse_percent,
    slug_from_url,
    text_or_none,
)

log = logging.getLogger(__name__)
BASE_URL = "https://www.hltv.org"


def _abs(href: Optional[str]) -> Optional[str]:
    return urljoin(BASE_URL, href) if href else None


def _stats_rows(tree: HTMLParser) -> dict[str, str]:
    """Parse the ``.stats-row`` key/value pairs HLTV uses on stat pages."""
    out: dict[str, str] = {}
    for row in tree.css(".stats-row"):
        spans = row.css("span")
        if len(spans) >= 2:
            key = text_or_none(spans[0])
            val = text_or_none(spans[-1])
            if key:
                out[key.lower()] = val or ""
    return out


def _highlight_boxes(tree: HTMLParser) -> dict[str, str]:
    """Parse the ``.standard-box .large-strong`` / ``.small-label-below`` pairs."""
    out: dict[str, str] = {}
    for box in tree.css(".standard-box .col, .standard-box .columns .col"):
        label = text_or_none(box.css_first(".small-label-below"))
        value = text_or_none(box.css_first(".large-strong"))
        if label and value:
            out[label.lower().strip()] = value
    return out


# ---------------------------------------------------------------------------
# Team overview  -- /stats/teams/{id}/{slug}
# ---------------------------------------------------------------------------
def parse_team_overview(html: str) -> dict:
    tree = HTMLParser(html)
    rows = _stats_rows(tree)
    highlights = _highlight_boxes(tree)

    def pick(*keys, transform=lambda v: v):
        for k in keys:
            for src in (highlights, rows):
                if k in src and src[k]:
                    return transform(src[k])
        return None

    return {
        "team_name": text_or_none(tree.css_first(".context-item-name"))
        or text_or_none(tree.css_first("h1")),
        "maps_played": pick("maps played", transform=parse_int),
        "wins": pick("wins", transform=parse_int),
        "draws": pick("draws", transform=parse_int),
        "losses": pick("losses", transform=parse_int),
        "total_kills": pick("total kills", transform=parse_int),
        "total_deaths": pick("total deaths", transform=parse_int),
        "rounds_played": pick("rounds played", transform=parse_int),
        "kd_ratio": pick("k/d ratio", "k/d", transform=parse_number),
        "win_rate_percent": pick("win rate", "win rate %", transform=parse_percent),
    }


# ---------------------------------------------------------------------------
# Team per-map stats  -- /stats/teams/maps/{id}/{slug}
#
# Returns a per-map breakdown with overall winrate plus CT and T side
# winrate when present. This is what powers "CT-side winrate over the last
# 5 months" style queries: the caller drives the time window via
# startDate / endDate query params on the URL.
# ---------------------------------------------------------------------------
def parse_team_map_stats(html: str) -> dict:
    tree = HTMLParser(html)
    maps: list[dict] = []

    for box in tree.css(".stats-team-maps .map-pool, .two-grid .col, .map-pool"):
        name = text_or_none(box.css_first(".map-pool-map-name, .map-name, .mapname"))
        if not name:
            continue
        stats: dict = {"map": name}

        for row in box.css(".stats-row"):
            spans = row.css("span")
            if len(spans) < 2:
                continue
            key = (text_or_none(spans[0]) or "").lower()
            val = text_or_none(spans[-1])
            if "win rate" in key:
                stats["win_rate_percent"] = parse_percent(val)
            elif "wins / draws / losses" in key or "w / d / l" in key:
                stats["wdl"] = val
            elif "total rounds" in key:
                stats["rounds_played"] = parse_int(val)
            elif "rounds won" in key:
                stats["rounds_won"] = parse_int(val)
            elif key == "ct" or key.startswith("ct ") or key.endswith(" ct"):
                stats["ct_round_win_percent"] = parse_percent(val)
            elif key == "t" or key.startswith("t ") or key.endswith(" t"):
                stats["t_round_win_percent"] = parse_percent(val)
            elif "times played" in key:
                stats["times_played"] = parse_int(val)

        for fact in box.css(".map-stats-infobox-stat"):
            label = (text_or_none(fact.css_first(".map-stats-infobox-type")) or "").lower()
            value = text_or_none(fact.css_first(".map-stats-infobox-stats-percentage"))
            if "ct" in label and "win" in label:
                stats["ct_round_win_percent"] = parse_percent(value)
            elif "t" in label and "win" in label and "ct" not in label:
                stats["t_round_win_percent"] = parse_percent(value)

        # Skip name-only matches from the top map-pool filter widget
        # (which also glues a percent onto the highlighted map, e.g.
        # "Train - 100.0%"). Keep only boxes carrying real per-map stats.
        if len(stats) > 1:
            maps.append(stats)

    overall_ct = None
    overall_t = None
    for label_node in tree.css(".small-label-below"):
        label = (text_or_none(label_node) or "").lower()
        sib = label_node.parent.css_first(".large-strong") if label_node.parent else None
        value = text_or_none(sib)
        if "ct" in label and overall_ct is None:
            overall_ct = parse_percent(value)
        elif "t side" in label and overall_t is None:
            overall_t = parse_percent(value)

    return {
        "overall_ct_round_win_percent": overall_ct,
        "overall_t_round_win_percent": overall_t,
        "maps": maps,
    }


# ---------------------------------------------------------------------------
# Team recent matches  -- /stats/teams/matches/{id}/{slug}
# ---------------------------------------------------------------------------
def parse_team_matches(html: str) -> list[dict]:
    tree = HTMLParser(html)
    matches: list[dict] = []
    for row in tree.css("table.stats-table tbody tr"):
        cells = row.css("td")
        if len(cells) < 5:
            continue
        date_cell = cells[0]
        opp_cell = cells[2] if len(cells) >= 6 else cells[1]
        map_cell = cells[3] if len(cells) >= 6 else cells[2]
        score_cell = cells[-2]
        result_cell = cells[-1]

        link = row.css_first("a[href*='/matches/']")
        href = link.attributes.get("href") if link else None
        matches.append(
            {
                "date": text_or_none(date_cell),
                "opponent": text_or_none(opp_cell),
                "map": text_or_none(map_cell),
                "score": text_or_none(score_cell),
                "result": (text_or_none(result_cell) or "").upper() or None,
                "match_url": _abs(href),
                "match_id": extract_id_from_url(href),
            }
        )
    return matches


# ---------------------------------------------------------------------------
# Player stats  -- /stats/players/{id}/{slug}
# ---------------------------------------------------------------------------
def parse_player_stats(html: str) -> dict:
    tree = HTMLParser(html)
    rows = _stats_rows(tree)
    highlights = _highlight_boxes(tree)

    def pick(*keys, transform=lambda v: v):
        for k in keys:
            for src in (highlights, rows):
                if k in src and src[k]:
                    return transform(src[k])
        return None

    return {
        "player_name": text_or_none(tree.css_first(".summaryNickname"))
        or text_or_none(tree.css_first("h1")),
        "real_name": text_or_none(tree.css_first(".summaryRealname")),
        "team": text_or_none(tree.css_first(".SummaryTeamname, .summaryTeamName")),
        "rating_2_0": pick("rating 2.0", "rating 1.0", transform=parse_number),
        "kd_diff": pick("kills - deaths", "k-d diff", transform=parse_number),
        "kd_ratio": pick("k/d ratio", transform=parse_number),
        "headshots_percent": pick("headshot %", "headshots", transform=parse_percent),
        "maps_played": pick("maps played", transform=parse_int),
        "rounds_played": pick("rounds played", transform=parse_int),
        "kills_per_round": pick("kills / round", transform=parse_number),
        "deaths_per_round": pick("deaths / round", transform=parse_number),
        "damage_per_round": pick("damage / round", transform=parse_number),
    }


# ---------------------------------------------------------------------------
# World rankings  -- /ranking/teams[/<YYYY>/<month>/<DD>]
# ---------------------------------------------------------------------------
def parse_rankings(html: str) -> list[dict]:
    tree = HTMLParser(html)
    out: list[dict] = []
    for card in tree.css(".ranked-team"):
        rank = parse_int(text_or_none(card.css_first(".position")))
        name = text_or_none(card.css_first(".teamLine .name, .name"))
        points = parse_int(text_or_none(card.css_first(".points")))
        link = card.css_first("a.moreLink, .teamLine a, a[href*='/team/']")
        href = link.attributes.get("href") if link else None
        out.append(
            {
                "rank": rank,
                "team": name,
                "points": points,
                "team_id": extract_id_from_url(href),
                "team_url": _abs(href),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Results  -- /results
# ---------------------------------------------------------------------------
def parse_results(html: str) -> list[dict]:
    tree = HTMLParser(html)
    out: list[dict] = []
    for con in tree.css(".result-con"):
        a = con.css_first("a.a-reset, a")
        href = a.attributes.get("href") if a else None
        teams = con.css(".team")
        score = con.css_first(".result-score")
        event = con.css_first(".event-name, .event")
        time = con.css_first(".date-cell, .time")
        if len(teams) < 2:
            continue
        out.append(
            {
                "team1": text_or_none(teams[0]),
                "team2": text_or_none(teams[1]),
                "score": text_or_none(score),
                "event": text_or_none(event),
                "time": text_or_none(time),
                "match_url": _abs(href),
                "match_id": extract_id_from_url(href),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Upcoming matches  -- /matches
# ---------------------------------------------------------------------------
def parse_upcoming_matches(html: str) -> list[dict]:
    tree = HTMLParser(html)
    out: list[dict] = []
    selectors = [
        ".upcomingMatch",
        ".liveMatch-container",
        ".match-day .match",
        ".matchListRow",
    ]
    for sel in selectors:
        for node in tree.css(sel):
            out.append(_parse_match_node(node))
        if out:
            break
    return [m for m in out if m.get("team1") or m.get("team2")]


def _parse_match_node(node: Node) -> dict:
    link = node.css_first("a[href*='/matches/']")
    href = link.attributes.get("href") if link else None
    teams = node.css(".matchTeamName, .team .matchTeamName, .team-name, .team")
    event = node.css_first(".matchEventName, .event-name, .event")
    time = node.css_first(".matchTime, .time, .date-cell")
    return {
        "team1": text_or_none(teams[0]) if len(teams) >= 1 else None,
        "team2": text_or_none(teams[1]) if len(teams) >= 2 else None,
        "event": text_or_none(event),
        "time": text_or_none(time),
        "stars": len(node.css(".stars i.fa-star")) or None,
        "match_url": _abs(href),
        "match_id": extract_id_from_url(href),
    }


# ---------------------------------------------------------------------------
# Generic team-by-name -> id resolver via search
# ---------------------------------------------------------------------------
def parse_team_links_from_search(html: str) -> list[dict]:
    tree = HTMLParser(html)
    out: list[dict] = []
    for a in tree.css("a[href*='/team/'], a[href*='/stats/teams/']"):
        href = a.attributes.get("href") or ""
        team_id = extract_id_from_url(href)
        if not team_id:
            continue
        out.append(
            {
                "team": text_or_none(a) or slug_from_url(href),
                "team_id": team_id,
                "slug": slug_from_url(href),
                "url": _abs(href),
            }
        )
    seen = set()
    deduped = []
    for row in out:
        if row["team_id"] in seen:
            continue
        seen.add(row["team_id"])
        deduped.append(row)
    return deduped
