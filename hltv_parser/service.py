"""High-level service layer.

Each method maps to a real HLTV URL pattern, builds the
``startDate``/``endDate`` query string, fetches the HTML through
:class:`HLTVClient`, and runs the matching parser. Returned values are
plain dicts/lists, ready to JSON-serialise for N8N or Make.
"""
from __future__ import annotations

import logging
from typing import Optional

from ._flag import gated
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

    # ---------------- teams ----------------
    @gated
    def team_overview(
        self,
        team_id: int,
        team_slug: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        months_back: Optional[int] = None,
    ) -> dict:
        s, e = normalise_date_range(start_date, end_date, months_back)
        path = f"/stats/teams/{team_id}/{team_slug}"
        html = self.client.get(path, params={"startDate": s, "endDate": e})
        data = parse_team_overview(html)
        data["team_id"] = team_id
        data["slug"] = team_slug
        data["window"] = {"start": s, "end": e}
        return data

    @gated
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
        path = f"/stats/teams/maps/{team_id}/{team_slug}"
        html = self.client.get(path, params={"startDate": s, "endDate": e})
        data = parse_team_map_stats(html)
        data["team_id"] = team_id
        data["slug"] = team_slug
        data["window"] = {"start": s, "end": e}
        return data

    @gated
    def team_matches(
        self,
        team_id: int,
        team_slug: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        months_back: Optional[int] = None,
    ) -> dict:
        s, e = normalise_date_range(start_date, end_date, months_back)
        path = f"/stats/teams/matches/{team_id}/{team_slug}"
        html = self.client.get(path, params={"startDate": s, "endDate": e})
        return {
            "team_id": team_id,
            "slug": team_slug,
            "window": {"start": s, "end": e},
            "matches": parse_team_matches(html),
        }

    @gated
    def find_team(self, name: str) -> list[dict]:
        html = self.client.get("/search", params={"term": name})
        return parse_team_links_from_search(html)

    # ---------------- players ----------------
    @gated
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
    @gated
    def rankings(self) -> list[dict]:
        html = self.client.get("/ranking/teams")
        return parse_rankings(html)

    @gated
    def upcoming_matches(self) -> list[dict]:
        html = self.client.get("/matches")
        return parse_upcoming_matches(html)

    @gated
    def results(self, offset: int = 0) -> list[dict]:
        params = {"offset": offset} if offset else None
        html = self.client.get("/results", params=params)
        return parse_results(html)
