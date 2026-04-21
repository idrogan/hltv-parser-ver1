"""HTTP client for escharts.com.

Same shape as the HLTV client — ``curl_cffi`` with Chrome TLS
impersonation, throttling, and exponential-backoff retries.
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

BASE_URL = "https://escharts.com"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class EsChartsError(Exception):
    """Non-recoverable EsportsCharts response."""


class EsChartsBlockedError(EsChartsError):
    """Cloudflare or rate-limit block."""


class EsChartsClient:
    def __init__(
        self,
        min_delay: float = 2.0,
        timeout: int = 30,
        impersonate: str = "chrome124",
        proxy: Optional[str] = None,
    ):
        self.min_delay = min_delay
        self.timeout = timeout
        self.impersonate = impersonate
        self.proxy = proxy
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

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        }

    @retry(
        reraise=True,
        retry=retry_if_exception_type(EsChartsBlockedError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
    )
    def get(self, path: str, params: Optional[dict] = None) -> str:
        url = path if path.startswith("http") else urljoin(BASE_URL, path)
        self._throttle()
        log.debug("GET %s params=%s", url, params)
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        try:
            resp = self._session.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
                impersonate=self.impersonate,
                proxies=proxies,
            )
        except Exception as exc:
            raise EsChartsBlockedError(f"network error: {exc}") from exc

        if resp.status_code in (403, 429, 503):
            raise EsChartsBlockedError(f"blocked: status={resp.status_code}")
        if resp.status_code >= 500:
            raise EsChartsBlockedError(f"upstream {resp.status_code}")
        if resp.status_code != 200:
            raise EsChartsError(f"unexpected status {resp.status_code} for {url}")

        body = resp.text
        if "Just a moment..." in body or "cf-browser-verification" in body:
            raise EsChartsBlockedError("Cloudflare challenge page returned")
        return body
