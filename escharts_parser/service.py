"""High-level EsportsCharts service."""
from __future__ import annotations

from typing import Optional

from .client import EsChartsClient
from .parsers import parse_tournament_detail, parse_tournament_list


class EsChartsService:
    def __init__(self, client: Optional[EsChartsClient] = None):
        self.client = client or EsChartsClient()

    def tournaments(self, game: str = "csgo", year: Optional[int] = None) -> list[dict]:
        """List tournaments (most-viewed first) for a game.

        Counter-Strike (including CS2-era events) lives under ``csgo`` on
        EsportsCharts; ``cs2`` is a 404. Other slugs: ``dota2``, ``lol``,
        ``valorant``, ``pubg``, ``rl``.
        """
        path = f"/tournaments/{game}"
        params = {"year": year} if year else None
        html = self.client.get(path, params=params)
        return parse_tournament_list(html)

    def tournament(self, game: str, slug: str) -> dict:
        """Fetch a single tournament page."""
        path = f"/tournaments/{game}/{slug}"
        html = self.client.get(path)
        return parse_tournament_detail(html)
