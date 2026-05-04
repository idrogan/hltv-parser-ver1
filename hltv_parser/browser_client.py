"""Bright Data Browser API client for HLTV.

Used when ``HLTV_BROWSER_WS`` is set. Connects to a remote Chromium over CDP
(``wss://brd-customer-...@brd.superproxy.io:9222``), navigates to the page,
and returns the rendered HTML. Exposes the same ``get(path, params, referer)``
signature as :class:`HLTVClient` so :class:`HLTVService` can swap them.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Optional
from urllib.parse import urlencode, urljoin

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .client import BASE_URL, HLTVBlockedError, HLTVError

log = logging.getLogger(__name__)


class HLTVBrowserClient:
    def __init__(
        self,
        ws_endpoint: str,
        min_delay: float = 2.0,
        timeout: int = 60,
    ):
        self.ws_endpoint = ws_endpoint
        self.min_delay = min_delay
        self.timeout_ms = timeout * 1000
        self._last_request_at = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.min_delay - elapsed
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.4))
            self._last_request_at = time.monotonic()

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
        url = path if path.startswith("http") else urljoin(BASE_URL, path)
        if params:
            url = f"{url}?{urlencode(params)}"
        self._throttle()
        log.debug("Browser GET %s", url)

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(
                    self.ws_endpoint, timeout=self.timeout_ms
                )
                try:
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = context.new_page()
                    if referer:
                        page.set_extra_http_headers({"Referer": referer})
                    resp = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
                    if resp is None:
                        raise HLTVBlockedError("no response from Browser API")
                    status = resp.status
                    if status in (403, 429, 503):
                        raise HLTVBlockedError(f"HLTV blocked: status={status}")
                    if status >= 500:
                        raise HLTVBlockedError(f"upstream {status}")
                    if status != 200:
                        raise HLTVError(f"unexpected status {status} for {url}")
                    body = page.content()
                finally:
                    browser.close()
        except (PWTimeoutError, PWError) as exc:
            raise HLTVBlockedError(f"browser error: {exc}") from exc

        if "Just a moment..." in body or "cf-browser-verification" in body:
            raise HLTVBlockedError("Cloudflare challenge page returned")
        return body
