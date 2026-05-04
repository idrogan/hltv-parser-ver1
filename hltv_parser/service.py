"""High-level service layer.

Each method maps to a real HLTV URL pattern, builds the
``startDate``/``endDate`` query string, fetches the HTML through
:class:`HLTVClient`, and runs the matching parser. Returned values are
plain dicts/lists, ready to JSON-serialise for N8N or Make.
"""
from __future__ import annotations

import logging
from typing import Optional

from .client import HLTVClient
from .parsers import (
    parse_player_stats,
    parse_rankings,
    parse_results,
    parse_team_links_from_search,
    parse_team_map_stats,
    parse_team_matches,
    parse_team_overview,
    parse_upcoming_matches,
)
from .util import normalise_date_range

log = logging.getLogger(__name__)


class HLTVService:
    def __init__(self, client: Optional[HLTVClient] = None):
        self.client = client or HLTVClient()
        self._warmed_teams: set[int] = set()

    # ---------------- teams ----------------
    def _team_referer(self, team_id: int, team_slug: str) -> str:
        # HLTV's WAF rejects direct hits on /stats/teams/... — visit the
        # team profile first so CF cookies + a realistic Referer are set,
        # mimicking the navigation a real user would take from search.
        # Smart proxies (Bright Data) handle CF per-request internally,
        # so warming is skipped to avoid doubling billed traffic.
        profile_path = f"/team/{team_id}/{team_slug}"
        if (
            not self.client.uses_smart_proxy
            and team_id not in self._warmed_teams
        ):
            try:
                self.client.get(profile_path)
                self._warmed_teams.add(team_id)
            except Exception:
                pass
        return f"https://www.hltv.org{profile_path}"

    def team_overview(
        self,
        team_id: int,
        team_slug: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        months_back: Optional[int] = None,
    ) -> dict:
        s, e = normalise_date_range(start_date, end_date, months_back)
        ref = self._team_referer(team_id, team_slug)
        path = f"/stats/teams/{team_id}/{team_slug}"
        html = self.client.get(path, params={"startDate": s, "endDate": e}, referer=ref)
        data = parse_team_overview(html)
        data["team_id"] = team_id
        data["slug"] = team_slug
        data["window"] = {"start": s, "end": e}
        return data

    def team_map_stats(
        self,
        team_id: int,
        team_slug: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        months_back: Optional[int] = None,
    ) -> dict:
        """Map breakdown for a team — includes CT/T side round winrate.

        ``months_back=5`` gives "the last 5 months" which is the headline
        use-case from the user's brief.
        """
        s, e = normalise_date_range(start_date, end_date, months_back)
        ref = self._team_referer(team_id, team_slug)
        path = f"/stats/teams/maps/{team_id}/{team_slug}"
        html = self.client.get(path, params={"startDate": s, "endDate": e}, referer=ref)
        data = parse_team_map_stats(html)
        data["team_id"] = team_id
        data["slug"] = team_slug
        data["window"] = {"start": s, "end": e}
        return data

    def team_matches(
        self,
        team_id: int,
        team_slug: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        months_back: Optional[int] = None,
    ) -> dict:
        s, e = normalise_date_range(start_date, end_date, months_back)
        ref = self._team_referer(team_id, team_slug)
        path = f"/stats/teams/matches/{team_id}/{team_slug}"
        html = self.client.get(path, params={"startDate": s, "endDate": e}, referer=ref)
        return {
            "team_id": team_id,
            "slug": team_slug,
            "window": {"start": s, "end": e},
            "matches": parse_team_matches(html),
        }

    def find_team(self, name: str) -> list[dict]:
        html = self.client.get("/search", params={"term": name})
        return parse_team_links_from_search(html)

    # ---------------- players ----------------
    def player_stats(
        self,
        player_id: int,
        player_slug: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        months_back: Optional[int] = None,
    ) -> dict:
        s, e = normalise_date_range(start_date, end_date, months_back)
        path = f"/stats/players/{player_id}/{player_slug}"
        html = self.client.get(path, params={"startDate": s, "endDate": e})
        data = parse_player_stats(html)
        data["player_id"] = player_id
        data["slug"] = player_slug
        data["window"] = {"start": s, "end": e}
        return data

    # ---------------- rankings / matches / results ----------------
    def rankings(self) -> list[dict]:
        html = self.client.get("/ranking/teams")
        return parse_rankings(html)

    def upcoming_matches(self) -> list[dict]:
        html = self.client.get("/matches")
        return parse_upcoming_matches(html)

    def results(self, offset: int = 0) -> list[dict]:
        params = {"offset": offset} if offset else None
        html = self.client.get("/results", params=params)
        return parse_results(html)
