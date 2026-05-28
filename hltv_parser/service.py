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
    parse_team_map_detail,
    parse_team_map_links,
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
        with_sides: bool = False,
    ) -> dict:
        """Map breakdown for a team.

        ``months_back=5`` gives "the last 5 months" which is the headline
        use-case from the user's brief. The maps overview itself carries no
        CT/T side round-win%; pass ``with_sides=True`` to follow each map's
        detail page and merge it in, at the cost of one extra fetch per map.
        """
        s, e = normalise_date_range(start_date, end_date, months_back)
        path = f"/stats/teams/maps/{team_id}/{team_slug}"
        html = self.client.get(path, params={"startDate": s, "endDate": e})
        data = parse_team_map_stats(html)
        data["team_id"] = team_id
        data["slug"] = team_slug
        data["window"] = {"start": s, "end": e}
        if with_sides:
            self._attach_side_winrates(html, data["maps"], s, e)
        return data

    def _attach_side_winrates(
        self, list_html: str, maps: list[dict], start: str, end: str
    ) -> None:
        """Fetch each played map's detail page and merge CT/T round-win%.

        Only maps present in ``maps`` are fetched (the ones with real data in
        the window), so cold maps in the switcher cost nothing.
        """
        links = {
            row["map"].lower(): row["detail_url"]
            for row in parse_team_map_links(list_html)
        }
        for m in maps:
            url = links.get((m.get("map") or "").lower())
            if not url:
                continue
            try:
                detail_html = self.client.get(url, params={"startDate": start, "endDate": end})
            except Exception as exc:  # noqa: BLE001 - one bad map shouldn't sink the rest
                log.warning("event=hltv_map_detail_failed map=%s err=%s", m.get("map"), exc)
                continue
            sides = parse_team_map_detail(detail_html)
            for key in ("ct_round_win_percent", "t_round_win_percent"):
                if sides.get(key) is not None:
                    m[key] = sides[key]

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
