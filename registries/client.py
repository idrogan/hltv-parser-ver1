"""Thin HTTP helper shared by the registry sources.

All four registries are official, free government/agency endpoints, so no
TLS impersonation or proxy rotation is needed — just a polite delay, an
honest User-Agent and retries on 429/5xx.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from curl_cffi import requests as cffi_requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


class RegistryError(Exception):
    """Non-recoverable registry response (bad id, 4xx, invalid JSON)."""


class RegistryBlockedError(RegistryError):
    """Rate-limited or temporarily unavailable (429 / 5xx / network)."""


class RegistryAuthError(RegistryError):
    """Missing or rejected API key (Companies House)."""


class HttpClient:
    def __init__(self, user_agent: str, min_delay: float = 0.5, timeout: int = 30):
        self.user_agent = user_agent
        self.min_delay = min_delay
        self.timeout = timeout
        self._last = 0.0
        self._lock = threading.Lock()
        self._session = cffi_requests.Session()

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    @retry(
        reraise=True,
        retry=retry_if_exception_type(RegistryBlockedError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
    )
    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[Any] = None,
        auth: Optional[tuple] = None,
        headers: Optional[dict] = None,
    ) -> Any:
        self._throttle()
        h = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if headers:
            h.update(headers)
        try:
            resp = self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                auth=auth,
                headers=h,
                timeout=self.timeout,
            )
        except Exception as exc:  # network / DNS / TLS
            raise RegistryBlockedError(f"network error for {url}: {exc}") from exc

        if resp.status_code in (429, 500, 502, 503, 504):
            raise RegistryBlockedError(f"{url} -> {resp.status_code}")
        if resp.status_code in (401, 403):
            raise RegistryAuthError(f"{url} -> {resp.status_code} (check API key / User-Agent)")
        if resp.status_code == 404:
            raise RegistryError(f"{url} -> 404 (unknown entity id?)")
        if resp.status_code != 200:
            raise RegistryError(f"{url} -> unexpected {resp.status_code}")
        try:
            return resp.json()
        except Exception as exc:
            raise RegistryError(f"invalid JSON from {url}: {exc}") from exc
