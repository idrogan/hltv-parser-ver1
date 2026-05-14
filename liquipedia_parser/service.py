"""Liquipedia service layer.

Path: public MediaWiki ``action=query`` (no API key needed). For each
tournament we want to track, we

  1. Discover candidate page titles via ``Category:S-Tier_Tournaments``
     and ``Category:A-Tier_Tournaments`` (these correspond to
     Liquipedia tier 1/2 in their current naming).
  2. Fetch wikitext per page (cached on disk by UTC day).
  3. Parse the Infobox league block for tournament metadata.
  4. Parse ``{{prize pool slot}}`` blocks for placements.

Match-result extraction from wikitext is meaningfully harder — the
match list templates nest map-by-map results and have many variants
across years. We surface a clear ``matches_from_wikitext_not_implemented``
event and return empty for now, so the rest of the pipeline still
populates tournaments + placements end-to-end on the first run.

Field names are best-effort against the current (2026-05) Liquipedia
CS infobox. If a query returns empty / partial, open the source
page's wikitext at ``?action=raw`` and confirm the field names.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from .client import LiquipediaClient
from .wikitext import (
    clean_value,
    extract_infobox_league,
    extract_prize_pool_slots,
    parse_money_usd,
)

log = logging.getLogger(__name__)


# Liquipedia uses two tier vocabularies: legacy numeric ("1", "2") and
# the modern "S-Tier" / "A-Tier" / "B-Tier" labels. We map S→1, A→2 so
# the rest of the pipeline can keep using numeric tiers.
TIER_CATEGORIES = {
    1: "S-Tier_Tournaments",
    2: "A-Tier_Tournaments",
}


def _parse_date(value: Any) -> Optional[str]:
    """Liquipedia infobox dates: 'YYYY-MM-DD' or 'Month D, YYYY'."""
    if not value:
        return None
    s = str(value).strip()
    if not s or s.startswith("1970-01-01") or s == "1900-01-01":
        return None
    # ISO already?
    try:
        return datetime.fromisoformat(s[:10]).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _status_for_dates(start: Optional[str], end: Optional[str]) -> str:
    today = date.today().isoformat()
    if end and end < today:
        return "finished"
    if start and start > today:
        return "upcoming"
    return "ongoing"


def _title_to_source_id(title: str) -> str:
    """Liquipedia URL convention: spaces → underscores. Stable across
    redirects because the client requests follow them upstream."""
    return title.replace(" ", "_")


class LiquipediaService:
    def __init__(self, client: Optional[LiquipediaClient] = None):
        self.client = client or LiquipediaClient()

    # ---- 1. Tournaments --------------------------------------------------

    def recent_tournaments(
        self,
        tier_max: int = 2,
        months_back: int = 12,
        months_forward: int = 6,
        limit: int = 100,
    ) -> list[dict]:
        """Tier 1/2 CS tournaments in a window around today.

        Returns row dicts already shaped for the ``tournaments`` table:
            source, source_id, name, tier, start_date, end_date,
            prize_pool_usd, location, status, raw_payload

        Network cost per run: 1 category-list call per tier (rare:
        paginated up to ~5x), plus 1 wikitext fetch per candidate page
        that passes the date window. Each request is throttled to 2 s
        floor — budget ~ 2 * tier_max + 2 * limit seconds wall time.
        """
        today = date.today()
        win_start = (today - timedelta(days=months_back * 31)).isoformat()
        win_end = (today + timedelta(days=months_forward * 31)).isoformat()

        # Step 1: discovery. Union of category memberships for the
        # tiers we care about. De-duplicate by title.
        candidate_titles: list[str] = []
        seen: set[str] = set()
        for tier in range(1, tier_max + 1):
            cat = TIER_CATEGORIES.get(tier)
            if not cat:
                continue
            try:
                members = self.client.category_members(cat)
            except Exception as e:
                log.warning(
                    "event=liquipedia_category_failed tier=%d cat=%s error=%r",
                    tier, cat, e,
                )
                continue
            for title in members:
                # Sub-pages like "PGL_Major_Copenhagen_2024/Main_Stage"
                # are not the canonical tournament — skip.
                if "/" in title:
                    continue
                if title in seen:
                    continue
                seen.add(title)
                candidate_titles.append(title)

        log.info(
            "event=liquipedia_candidates count=%d tier_max=%d",
            len(candidate_titles), tier_max,
        )

        # Step 2: fetch + parse each candidate's infobox. Stop once we
        # have `limit` rows that match the window.
        out: list[dict] = []
        for title in candidate_titles:
            if len(out) >= limit:
                break
            try:
                wikitext = self.client.query_revisions(title)
            except Exception as e:
                log.warning(
                    "event=liquipedia_wikitext_failed title=%r error=%r",
                    title, e,
                )
                continue
            if not wikitext:
                continue
            infobox = extract_infobox_league(wikitext)
            if not infobox:
                continue

            sdate = _parse_date(infobox.get("sdate") or infobox.get("startdate"))
            edate = _parse_date(infobox.get("edate") or infobox.get("enddate"))
            # Window filter: a tournament is in-window if either bound
            # falls in [win_start, win_end]. Open-ended ones (no edate
            # yet, upcoming) are kept if sdate is in-window.
            in_window = False
            if sdate and win_start <= sdate <= win_end:
                in_window = True
            elif edate and win_start <= edate <= win_end:
                in_window = True
            if not in_window:
                continue

            tier_raw = (
                infobox.get("liquipediatier")
                or infobox.get("tier")
                or ""
            ).strip()

            prize_usd = (
                parse_money_usd(infobox.get("prizepoolusd") or "")
                or parse_money_usd(infobox.get("prizepool") or "")
            )

            location = clean_value(
                infobox.get("location")
                or infobox.get("city")
                or infobox.get("country")
                or infobox.get("venue")
                or ""
            ) or None

            name = clean_value(infobox.get("name") or "") or title

            out.append({
                "source": "liquipedia",
                "source_id": _title_to_source_id(title),
                "name": name,
                "tier": tier_raw or None,
                "start_date": sdate,
                "end_date": edate,
                "prize_pool_usd": prize_usd,
                "location": location,
                "status": _status_for_dates(sdate, edate),
                "raw_payload": {"title": title, "infobox": infobox},
            })
        log.info("event=liquipedia_tournaments count=%d", len(out))
        return out

    # ---- 2. Prize distribution -------------------------------------------

    def prize_distribution(self, page_name: str) -> list[dict]:
        """Placements for one tournament page.

        ``page_name`` is the underscored Liquipedia title — the same
        value we wrote as ``source_id`` for the tournament.
        """
        title = page_name.replace("_", " ")
        try:
            wikitext = self.client.query_revisions(title)
        except Exception as e:
            log.warning(
                "event=liquipedia_prize_fetch_failed page=%r error=%r",
                page_name, e,
            )
            return []
        if not wikitext:
            return []

        slots = extract_prize_pool_slots(wikitext)
        out: list[dict] = []
        for slot in slots:
            participants = slot["participants"] or [None]
            # Liquipedia stacks tied placements into a single slot with
            # multiple participants ("4-5", participants=[A, B]). We
            # emit one row per participant so the consumer can fan
            # out cleanly.
            for participant in participants:
                if not participant:
                    continue
                out.append({
                    "place": slot["place"],
                    "team_or_player": participant,
                    "amount_usd": slot["usd"],
                    "points": None,
                    "notes": None,
                })
        log.info(
            "event=liquipedia_prize_dist page=%s count=%d",
            page_name, len(out),
        )
        return out

    # ---- 3. Match results ------------------------------------------------

    def match_results(self, page_name: str, limit: int = 200) -> list[dict]:
        """Match results for one tournament page.

        Not implemented on the wikitext path yet — the match-list
        templates are wildly variable across years (Match maps, Match
        legacy, MatchListStart + MatchMaps + MatchListEnd, ...). The
        cargo-table path that previously powered this method requires
        the LiquipediaDB API key we explicitly chose not to use.

        For match-level data, PandaScore is the canonical source in
        this pipeline. Returning empty keeps the runner idempotent.
        """
        log.info(
            "event=liquipedia_matches_not_implemented page=%s", page_name,
        )
        return []
