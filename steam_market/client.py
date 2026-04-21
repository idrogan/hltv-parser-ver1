"""HTTP client for the Steam Community Market.

No TLS impersonation needed — plain requests work fine — but Valve
429s hard if you burst. We throttle to one request every
``STEAM_MIN_DELAY`` seconds (default 3.5) and retry with backoff on
429/5xx.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Any, Optional
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

BASE_URL = "https://steamcommunity.com"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class SteamError(Exception):
    """Non-recoverable Steam response."""


class SteamBlockedError(SteamError):
    """Rate-limited or otherwise blocked by Steam (429/5xx)."""


class SteamClient:
    """Polite JSON client for ``steamcommunity.com/market``.

    Parameters
    ----------
    min_delay:
        Minimum seconds between requests. Valve will 429 anything faster
        than ~3 req/s sustained; 3.5s is safe for background workers.
    login_secure_cookie:
        Optional ``steamLoginSecure`` cookie value. Only needed for the
        ``pricehistory`` endpoint (full lifetime sales).
    """

    def __init__(
        self,
        min_delay: float = 3.5,
        timeout: int = 20,
        proxy: Optional[str] = None,
        login_secure_cookie: Optional[str] = None,
    ):
        self.min_delay = min_delay
        self.timeout = timeout
        self.proxy = proxy
        self.login_secure_cookie = login_secure_cookie
        self._last_request_at = 0.0
        self._lock = threading.Lock()
        self._session = cffi_requests.Session()

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.min_delay - elapsed
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.3))
            self._last_request_at = time.monotonic()

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json,text/javascript,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://steamcommunity.com/market/",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _cookies(self) -> Optional[dict]:
        if self.login_secure_cookie:
            return {"steamLoginSecure": self.login_secure_cookie}
        return None

    @retry(
        reraise=True,
        retry=retry_if_exception_type(SteamBlockedError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
    )
    def get_json(self, path: str, params: Optional[dict] = None) -> Any:
        url = path if path.startswith("http") else urljoin(BASE_URL, path)
        self._throttle()
        log.debug("GET %s params=%s", url, params)

        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        try:
            resp = self._session.get(
                url,
                params=params,
                headers=self._headers(),
                cookies=self._cookies(),
                timeout=self.timeout,
                proxies=proxies,
            )
        except Exception as exc:
            raise SteamBlockedError(f"network error: {exc}") from exc

        if resp.status_code in (429, 502, 503):
            raise SteamBlockedError(f"Steam rate-limit: status={resp.status_code}")
        if resp.status_code == 401:
            raise SteamError("steam 401 — login cookie missing or expired")
        if resp.status_code != 200:
            raise SteamError(f"unexpected status {resp.status_code} for {url}")

        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise SteamError(f"invalid JSON from {url}: {exc}") from exc
