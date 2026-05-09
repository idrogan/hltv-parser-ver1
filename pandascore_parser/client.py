"""PandaScore HTTP client.

PandaScore is a paid API but ships a free hobbyist tier with a small
monthly request budget — verified against the actual response headers
on first run. The CS endpoint group has historically lived under
``/csgo/...`` and survived the CS2 rename; if PandaScore migrates to a
``/cs2/...`` namespace later we'll detect 404s and switch.

Auth & rate limit shape:

  * ``Authorization: Bearer <PANDASCORE_API_KEY>``
  * Per-response headers: ``X-Rate-Limit-Limit``, ``X-Rate-Limit-Remaining``
    (per-minute window). PandaScore also enforces a separate monthly
    budget visible only in their dashboard, so the runner persists
    ``Remaining`` into ``_scraper_runs.meta`` for retro graphing.
  * Pagination: ``?page=N&per_page=K`` (per_page max 100).

Soft-stop heuristic: refuse new requests once the per-minute
``Remaining`` drops below 10% of ``Limit``. This is a per-minute
window, not the monthly budget — if the monthly budget is the binding
constraint we'll see it as 429s and surface them cleanly.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterable, Optional

import httpx

log = logging.getLogger(__name__)

BASE_URL = os.getenv("PANDASCORE_BASE_URL", "https://api.pandascore.co")


class PandaScoreError(RuntimeError):
    """Generic PandaScore failure (non-retryable)."""


class PandaScoreQuotaExhausted(PandaScoreError):
    """Raised on 429 or when the per-minute remaining budget hits 0."""


class PandaScoreClient:
    """Thin httpx client with auth, rate-limit awareness, and pagination."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        soft_stop_ratio: float = 0.10,
    ):
        key = api_key or os.getenv("PANDASCORE_API_KEY", "").strip()
        if not key:
            raise PandaScoreError(
                "PANDASCORE_API_KEY is required for PandaScore requests "
                "but is not set. Sign in at https://app.pandascore.co/ "
                "and copy the token from Account → API."
            )
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )
        self.soft_stop_ratio = soft_stop_ratio
        # Latest rate-limit observation. Updated on every successful response.
        self.rate_limit_limit: Optional[int] = None
        self.rate_limit_remaining: Optional[int] = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- internals -------------------------------------------------------

    def _update_rate_limit(self, headers: httpx.Headers) -> None:
        try:
            self.rate_limit_limit = int(headers.get("X-Rate-Limit-Limit", 0)) or None
            self.rate_limit_remaining = int(
                headers.get("X-Rate-Limit-Remaining", 0)
            )
            log.debug(
                "event=pandascore_rate_limit limit=%s remaining=%s",
                self.rate_limit_limit, self.rate_limit_remaining,
            )
        except (ValueError, TypeError):
            pass  # absent or malformed headers — non-fatal

    def _check_soft_stop(self) -> None:
        if (
            self.rate_limit_limit
            and self.rate_limit_remaining is not None
            and self.rate_limit_remaining
            < max(1, int(self.rate_limit_limit * self.soft_stop_ratio))
        ):
            raise PandaScoreQuotaExhausted(
                f"PandaScore soft-stop: remaining={self.rate_limit_remaining} "
                f"of limit={self.rate_limit_limit} "
                f"(<{int(self.soft_stop_ratio * 100)}%)"
            )

    # ---- public ---------------------------------------------------------

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> tuple[list[dict], httpx.Headers]:
        self._check_soft_stop()
        try:
            r = self._client.get(path, params=params or {})
        except httpx.HTTPError as e:
            raise PandaScoreError(f"network error: {e}") from e

        if r.status_code == 401:
            raise PandaScoreError("PandaScore 401 — check PANDASCORE_API_KEY")
        if r.status_code == 429:
            self._update_rate_limit(r.headers)
            retry_after = r.headers.get("Retry-After")
            raise PandaScoreQuotaExhausted(
                f"PandaScore 429; Retry-After={retry_after}"
            )
        if r.status_code >= 500:
            raise PandaScoreError(f"PandaScore upstream {r.status_code}")
        if r.status_code != 200:
            raise PandaScoreError(
                f"PandaScore {r.status_code} on {path}: {r.text[:200]}"
            )

        self._update_rate_limit(r.headers)
        try:
            payload = r.json()
        except ValueError as e:
            raise PandaScoreError(f"non-JSON response: {e}") from e

        if isinstance(payload, dict) and "error" in payload:
            raise PandaScoreError(f"PandaScore error: {payload['error']}")

        if not isinstance(payload, list):
            raise PandaScoreError(
                f"unexpected payload type {type(payload).__name__} on {path}"
            )
        return payload, r.headers

    def paginate(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        per_page: int = 100,
        max_pages: int = 20,
        delay: float = 0.5,
    ) -> Iterable[dict]:
        """Yield rows across pages; stops on empty page or max_pages."""
        params = dict(params or {})
        params["per_page"] = per_page
        for page in range(1, max_pages + 1):
            params["page"] = page
            log.info(
                "event=pandascore_request path=%s page=%d per_page=%d",
                path, page, per_page,
            )
            rows, _ = self.get(path, params=params)
            if not rows:
                return
            for row in rows:
                yield row
            if len(rows) < per_page:
                return
            time.sleep(delay)
