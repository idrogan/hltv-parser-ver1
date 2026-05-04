"""HTTP client for HLTV.org.

HLTV sits behind Cloudflare and aggressively rate-limits scrapers, so the
client here:

  * Uses curl_cffi with a Chrome TLS fingerprint to pass JA3 checks.
  * Rotates through a small pool of realistic User-Agents.
  * Retries with exponential backoff on 403/429/5xx.
  * Throttles requests with a configurable minimum delay.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Optional
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

BASE_URL = "https://www.hltv.org"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class HLTVError(Exception):
    """Raised when HLTV returns a non-recoverable response."""


class HLTVBlockedError(HLTVError):
    """Raised when Cloudflare blocks the request (403/503 with challenge)."""


class HLTVClient:
    """Thin wrapper around curl_cffi with throttling and retries.

    Parameters
    ----------
    min_delay:
        Minimum seconds between requests. HLTV expects polite traffic;
        going below ~1.5s reliably trips the WAF.
    timeout:
        Per-request timeout in seconds.
    impersonate:
        curl_cffi browser profile to imitate. ``chrome124`` works as of
        early 2026.
    proxy:
        Optional proxy URL (``http://user:pass@host:port``).
    """

    def __init__(
        self,
        min_delay: float = 2.0,
        timeout: int = 30,
        impersonate: str = "chrome124",
        proxy: Optional[str] = None,
        flaresolverr_url: Optional[str] = None,
    ):
        self.min_delay = min_delay
        self.timeout = timeout
        self.impersonate = impersonate
        self.proxy = proxy
        self.flaresolverr_url = flaresolverr_url.rstrip("/") if flaresolverr_url else None
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

    def _headers(self, referer: Optional[str] = None) -> dict:
        h = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none" if not referer else "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        if referer:
            h["Referer"] = referer
        return h

    def _fetch_via_flaresolverr(
        self, url: str, params: Optional[dict]
    ) -> tuple[int, str]:
        from urllib.parse import urlencode

        full_url = url
        if params:
            sep = "&" if "?" in url else "?"
            full_url = f"{url}{sep}{urlencode(params)}"
        payload = {
            "cmd": "request.get",
            "url": full_url,
            "maxTimeout": 60000,
        }
        try:
            resp = cffi_requests.post(
                f"{self.flaresolverr_url}/v1",
                json=payload,
                timeout=self.timeout + 60,
            )
        except Exception as exc:
            raise HLTVBlockedError(f"flaresolverr unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise HLTVBlockedError(
                f"flaresolverr returned {resp.status_code}"
            )
        data = resp.json()
        if data.get("status") != "ok":
            raise HLTVBlockedError(
                f"flaresolverr: {data.get('message') or 'unknown error'}"
            )
        sol = data.get("solution") or {}
        return int(sol.get("status") or 0), sol.get("response") or ""

    def _fetch_direct(
        self,
        url: str,
        params: Optional[dict],
        referer: Optional[str],
    ) -> tuple[int, str]:
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        try:
            resp = self._session.get(
                url,
                params=params,
                headers=self._headers(referer=referer),
                timeout=self.timeout,
                impersonate=self.impersonate,
                proxies=proxies,
            )
        except Exception as exc:
            raise HLTVBlockedError(f"network error: {exc}") from exc
        return resp.status_code, resp.text

    @retry(
        reraise=True,
        retry=retry_if_exception_type(HLTVBlockedError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
    )
    def get(
        self,
        path: str,
        params: Optional[dict] = None,
        referer: Optional[str] = None,
    ) -> str:
        """GET ``path`` (relative or absolute) and return raw HTML."""
        url = path if path.startswith("http") else urljoin(BASE_URL, path)
        self._throttle()
        log.debug("GET %s params=%s flaresolverr=%s", url, params, bool(self.flaresolverr_url))

        if self.flaresolverr_url:
            status, body = self._fetch_via_flaresolverr(url, params)
        else:
            status, body = self._fetch_direct(url, params, referer)

        if status in (403, 429, 503):
            raise HLTVBlockedError(f"HLTV blocked request: status={status}")
        if status >= 500:
            raise HLTVBlockedError(f"upstream {status}")
        if status != 200:
            raise HLTVError(f"unexpected status {status} for {url}")
        if "Just a moment..." in body or "cf-browser-verification" in body:
            raise HLTVBlockedError("Cloudflare challenge page returned")
        return body
