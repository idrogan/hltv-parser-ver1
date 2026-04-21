"""EsportsCharts (escharts.com) parser.

Scrapes public tournament viewership pages: peak viewers, average
viewers, hours watched, airtime. No official API key needed — they
sell a paid API separately, which is worth considering for heavy
commercial use (https://dashboard.escharts.com/).
"""

from .client import EsChartsBlockedError, EsChartsClient, EsChartsError
from .service import EsChartsService

__all__ = [
    "EsChartsBlockedError",
    "EsChartsClient",
    "EsChartsError",
    "EsChartsService",
]
__version__ = "0.1.0"
