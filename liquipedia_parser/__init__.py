"""Liquipedia counterstrike-wiki API parser.

Polite, single-process MediaWiki API client + a thin service layer that
maps to our `tournaments`, `tournament_prize_distribution`, and
`matches` tables. See ``client.py`` for the full TOS quote.
"""
from .client import LiquipediaClient, LiquipediaError, LiquipediaRateLimited
from .service import LiquipediaService

__all__ = [
    "LiquipediaClient",
    "LiquipediaError",
    "LiquipediaRateLimited",
    "LiquipediaService",
]
