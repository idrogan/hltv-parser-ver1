"""CloakBrowser-backed HLTV fetch client.

Drop-in replacement for ``HLTVClient`` that swaps the (Cloudflare-blocked)
curl_cffi fetch for a CloakBrowser stealth-Chromium fetch. It exposes the
SAME ``get(path, params, referer) -> html`` contract, so ``HLTVService``
and every parser in ``parsers.py`` work unchanged — only the transport
differs.

Why this exists: HLTV's Cloudflare WAF blocks curl_cffi within minutes
(see docs/RESUMING_HLTV.md). A spike (scripts/cloak_poc.py) proved
CloakBrowser pierces it (HTTP 200, full rendered DOM, 3/3 rounds). This
client is the productionised version of that finding.

Operational notes:
  * ``cloakbrowser`` is imported lazily so the rest of the app/CLI never
    requires the heavy Chromium dependency unless this backend is chosen.
  * One browser is launched lazily and reused across ``get()`` calls
    (a fresh Chromium per request costs ~9s). Cleanup is registered with
    atexit so a CLI run doesn't leave a zombie browser.
  * Same ``HLTVError`` / ``HLTVBlockedError`` taxonomy as the curl_cffi
    client, so callers don't special-case the backend.
  * Still throttled. A real browser is slower than curl, but politeness
    to the WAF (and to HLTV) is the whole point.

Deployment caveat: this was validated from a residential IP. A
datacenter egress (the DO droplet) has worse Cloudflare reputation —
run this behind a residential proxy or on a residential host, NOT the
shared droplet egress that PandaScore/Steam depend on.
"""
from __future__ import annotations

import atexit
import logging
import random
import re
import threading
import time
from typing import Optional
from urllib.parse import urlencode, urljoin

from .client import BASE_URL, HLTVBlockedError, HLTVError

log = logging.getLogger(__name__)

# Cloudflare interstitial detection — keyed off the small challenge page's
# <title>, NOT on bare "turnstile"/"challenge-platform" substrings (those
# are benign cdn-cgi script tags present even on a passed page; matching
# them false-flagged the 11 MB real HLTV page in the spike's first run).
_INTERSTITIAL_TITLES = (
    "just a moment",
    "attention required",
    "access denied",
    "checking your browser",
    "verifying you are human",
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S)


def _looks_blocked(html: str) -> bool:
    low = html.lower()
    m = _TITLE_RE.search(low)
    title = m.group(1).strip() if m else ""
    if any(t in title for t in _INTERSTITIAL_TITLES):
        return True
    # A challenge body carrying the cf_chl token is tiny; a real page isn't.
    return "cf_chl" in low and len(html) < 50_000


class HLTVCloakClient:
    """HLTV fetch via CloakBrowser. Same .get() contract as HLTVClient."""

    def __init__(
        self,
        min_delay: float = 4.0,
        timeout: int = 60,
        settle_s: float = 6.0,
        headless: bool = True,
    ):
        self.min_delay = min_delay
        self.timeout = timeout            # seconds
        self.settle_s = settle_s
        self.headless = headless
        self._last_request_at = 0.0
        self._lock = threading.Lock()
        self._browser = None              # lazily launched
        self._launch_lock = threading.Lock()
        atexit.register(self.close)

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.min_delay - elapsed
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.4))
            self._last_request_at = time.monotonic()

    def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        with self._launch_lock:
            if self._browser is None:
                try:
                    from cloakbrowser import launch
                except ImportError as exc:
                    raise HLTVError(
                        "HLTV cloak backend selected but 'cloakbrowser' is "
                        "not installed. `pip install cloakbrowser` (first "
                        "launch also downloads the stealth Chromium)."
                    ) from exc
                log.info("event=hltv_cloak_launch headless=%s", self.headless)
                self._browser = launch(headless=self.headless)
        return self._browser

    def close(self) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

    def get(
        self,
        path: str,
        params: Optional[dict] = None,
        referer: Optional[str] = None,
    ) -> str:
        """GET ``path`` (relative or absolute) and return rendered HTML."""
        url = path if path.startswith("http") else urljoin(BASE_URL, path)
        if params:
            # Drop None values, mirror curl_cffi's query encoding.
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urlencode(clean)}"

        self._throttle()
        browser = self._ensure_browser()
        log.debug("cloak GET %s", url)

        page = browser.new_page()
        try:
            if referer:
                try:
                    page.set_extra_http_headers({"Referer": referer})
                except Exception:
                    pass
            try:
                resp = page.goto(
                    url,
                    timeout=self.timeout * 1000,
                    wait_until="domcontentloaded",
                )
            except Exception as exc:
                raise HLTVBlockedError(f"navigation error: {exc}") from exc

            # Let any Turnstile / JS challenge auto-resolve before snapshot.
            page.wait_for_timeout(int(self.settle_s * 1000))
            html = page.content()
        finally:
            try:
                page.close()
            except Exception:
                pass

        status = resp.status if resp is not None else -1
        # A real challenge keeps the interstitial in the DOM even after
        # settle; a hard upstream/edge block shows up as 5xx with no
        # rendered content. We trust the DOM over the status code because
        # CloakBrowser solves the challenge after the initial response
        # (the spike saw EsC return 403 on the nav response yet deliver a
        # real post-solve DOM).
        if _looks_blocked(html):
            raise HLTVBlockedError(
                f"Cloudflare challenge still present (status={status}) for {url}"
            )
        if status >= 500 and len(html) < 50_000:
            raise HLTVBlockedError(f"upstream {status} for {url}")
        return html
