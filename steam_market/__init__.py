"""Steam Community Market parser.

Hits Valve's public (if undocumented) market JSON endpoints:

* ``priceoverview`` — current lowest/median price + 24-hour sold volume
* ``search/render`` — browse items by query with listing counts
* ``itemordershistogram`` — buy/sell order book depth
* ``pricehistory`` — full per-sale history (requires a login cookie)

No Cloudflare, but Valve rate-limits aggressively (~20 req/min).
The default throttle is 3.5s between calls.
"""

from .client import SteamClient, SteamBlockedError, SteamError
from .service import SteamMarketService

__all__ = ["SteamClient", "SteamBlockedError", "SteamError", "SteamMarketService"]
__version__ = "0.1.0"

CS2_APPID = 730
CSGO_APPID = 730
DOTA2_APPID = 570
TF2_APPID = 440
