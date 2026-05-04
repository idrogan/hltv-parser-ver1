"""ScrapFly client for HLTV.

Used when ``HLTV_SCRAPFLY_KEY`` is set. Routes requests through
``api.scrapfly.io/scrape`` with anti-scraping-protection (``asp=true``) and
JS rendering. Same ``get(path, params, referer)`` signature as
:class:`HLTVClient` so :class:`HLTVService` can swap them transparently.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Optional
from urllib.parse import urlencode, urljoin

from curl_cffi import requests as cffi_requests

from .client import BASE_URL, HLTVBlockedError, HLTVError

log = logging.getLogger(__name__)

SCRAPFLY_ENDPOINT = "https://api.scrapfly.io/scrape"


class HLTVScrapflyClient:
    def __init__(
        self,
        api_key: str,
        min_delay: float = 1.0,
        timeout: int = 90,
        country: str = "us",
    ):
        self.api_key = api_key
        self.min_delay = min_delay
        self.timeout = timeout
        self.country = country
        self._last_request_at = 0.0
        self._lock = threading.Lock()
        self._session = cffi_requests.Session()

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.min_delay - elapsed
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.4))
            self._last_request_at = time.monotonic()

    def get(
        self,
        path: str,
        params: Optional[dict] = None,
        referer: Optional[str] = None,
    ) -> str:
        target = path if path.startswith("http") else urljoin(BASE_URL, path)
        if params:
            target = f"{target}?{urlencode(params)}"

        sf_params = {
            "key": self.api_key,
            "url": target,
            "asp": "true",
            "render_js": "true",
            "country": self.country,
        }
        self._throttle()
        log.debug("ScrapFly GET %s", target)

        try:
            resp = self._session.get(
                SCRAPFLY_ENDPOINT, params=sf_params, timeout=self.timeout
            )
        except Exception as exc:
            raise HLTVBlockedError(f"scrapfly network error: {exc}") from exc

        if resp.status_code == 429:
            raise HLTVBlockedError("scrapfly rate-limited (429)")
        if resp.status_code >= 500:
            raise HLTVBlockedError(f"scrapfly upstream {resp.status_code}")

        try:
            data = resp.json()
        except Exception as exc:
            raise HLTVError(
                f"scrapfly non-json response (status={resp.status_code})"
            ) from exc

        result = data.get("result") or {}
        upstream_status = result.get("status_code")
        body = result.get("content") or ""

        if resp.status_code != 200:
            err = data.get("message") or data.get("error") or resp.text[:200]
            if upstream_status in (403, 429, 503):
                raise HLTVBlockedError(
                    f"hltv blocked via scrapfly: status={upstream_status} ({err})"
                )
            raise HLTVError(f"scrapfly error {resp.status_code}: {err}")

        if upstream_status in (403, 429, 503):
            raise HLTVBlockedError(f"hltv blocked: status={upstream_status}")
        if upstream_status and upstream_status >= 500:
            raise HLTVBlockedError(f"hltv upstream {upstream_status}")
        if upstream_status and upstream_status != 200:
            raise HLTVError(f"unexpected hltv status {upstream_status}")

        if "Just a moment..." in body or "cf-browser-verification" in body:
            raise HLTVBlockedError("Cloudflare challenge page returned")
        return body
