"""HLTV.org parser library.

Public surface kept intentionally small so it can be embedded in scripts,
N8N "Execute Command" nodes, or wrapped behind the bundled FastAPI server.
"""

from .client import HLTVClient
from .parsers import (
    parse_team_overview,
    parse_team_map_stats,
    parse_team_matches,
    parse_player_stats,
    parse_rankings,
    parse_upcoming_matches,
    parse_results,
)
from .service import HLTVService

__all__ = [
    "HLTVClient",
    "HLTVService",
    "parse_team_overview",
    "parse_team_map_stats",
    "parse_team_matches",
    "parse_player_stats",
    "parse_rankings",
    "parse_upcoming_matches",
    "parse_results",
]

__version__ = "0.1.0"
