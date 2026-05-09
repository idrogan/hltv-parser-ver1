"""Liquipedia API client.

Two-API design:

A. **Public MediaWiki API** at https://liquipedia.net/counterstrike/api.php
   * 1 request / 2 sec across the whole client (TOS).
   * action=parse capped at 1 / 30 sec — resource-intensive.
   * action=cargoquery is NOT exposed here (verified 2026-05-09: returns
     ``badvalue: Unrecognized value for parameter "action"``).
   * Custom User-Agent with project + contact email mandatory.
   * gzip support mandatory.

B. **LiquipediaDB API** (a.k.a. api.liquipedia.net)
   * Requires registration → approval → API key.
   * Free tier: 60 requests / hour.
   * Exposes structured Cargo queries (the equivalent of action=cargoquery).
   * Auth scheme: documented behind the LiquipediaDB Dashboard login;
     left as a small adapter in this client so it can be filled in
     accurately the moment we have the docs (see ``cargoquery``).

Until ``LIQUIPEDIA_API_KEY`` is set, ``cargoquery`` raises a clear
error pointing at registration. The MediaWiki path (parse, query) is
reserved for things that don't require structured cargo.

TOS reference (verbatim summary, fetched 2026-05-09 from
https://liquipedia.net/api-terms-of-use):

  1. Rate limit ALL HTTP requests to no more than 1 request per 2 seconds.
  2. action=parse requests must not exceed 1 / 30 seconds.
  3. User-Agent: ``ProjectName/version (https://example.com/; you@example.com)``
  4. HTTP client must accept Content-Encoding: gzip.
  5. Reuse the HTTP client across requests.
  6. Authenticated calls only when needed.
  7. Violations → automated temporary IP bans (we hit one during dev).
  8. LiquipediaDB API: ≤ 60 req/hour, credentials issued on approval.

Plus our own additions:

  * Filesystem cache keyed by query + UTC day so re-runs in the same
    day don't re-fetch.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

API_URL = "https://liquipedia.net/counterstrike/api.php"

# TOS rate floor: 1 request per 2 seconds across the whole client.
DEFAULT_MIN_DELAY = float(os.getenv("LIQUIPEDIA_MIN_DELAY", "2.0"))

# Filesystem cache root. Configurable mostly so tests can isolate.
DEFAULT_CACHE_DIR = Path(os.getenv("LIQUIPEDIA_CACHE_DIR", ".cache/liquipedia"))


class LiquipediaError(RuntimeError):
    """Generic Liquipedia API failure."""


class LiquipediaRateLimited(LiquipediaError):
    """429 from Liquipedia (or a Retry-After response)."""


def _build_user_agent() -> str:
    """Build the TOS-compliant UA string from env.

    ``LIQUIPEDIA_CONTACT_EMAIL`` is mandatory; the brief explicitly
    requires it. Refusing to start without it is the polite default.
    """
    email = os.getenv("LIQUIPEDIA_CONTACT_EMAIL", "").strip()
    if not email:
        raise LiquipediaError(
            "LIQUIPEDIA_CONTACT_EMAIL is required by Liquipedia API TOS "
            "(custom User-Agent must include a contact). Set it in .env."
        )
    project_url = os.getenv(
        "LIQUIPEDIA_PROJECT_URL",
        "https://github.com/idrogan/hltv-parser-ver1",
    )
    version = os.getenv("LIQUIPEDIA_UA_VERSION", "0.1")
    return f"hltv-parser-ver1/{version} ({project_url}; {email})"


class LiquipediaClient:
    """Polite MediaWiki API client with on-disk per-day cache."""

    def __init__(
        self,
        min_delay: float = DEFAULT_MIN_DELAY,
        cache_dir: Optional[Path] = None,
        timeout: float = 30.0,
    ):
        self.min_delay = max(min_delay, 2.0)  # hard floor: TOS says 1 per 2s
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": _build_user_agent(),
                "Accept-Encoding": "gzip",
                "Accept": "application/json",
            },
        )
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- internals -------------------------------------------------------

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.min_delay - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()

    def _cache_path(self, params: dict[str, Any]) -> Path:
        # Day-grain cache: identical query within the same UTC day = same key.
        day = time.strftime("%Y-%m-%d", time.gmtime())
        digest = hashlib.sha256(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]
        return self.cache_dir / day / f"{digest}.json"

    # ---- public ---------------------------------------------------------

    def cargoquery(
        self,
        *,
        tables: str,
        fields: str,
        where: Optional[str] = None,
        order_by: Optional[str] = None,
        group_by: Optional[str] = None,
        join_on: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """Run a Cargo SQL-ish query via the LiquipediaDB API.

        Requires ``LIQUIPEDIA_API_KEY``. Public MediaWiki endpoint does
        NOT expose action=cargoquery (verified 2026-05-09).

        Auth scheme is filled in once the LiquipediaDB Dashboard docs
        are available — this raises with an instructive message until
        then. See module docstring for context.
        """
        api_key = os.getenv("LIQUIPEDIA_API_KEY", "").strip()
        if not api_key:
            raise LiquipediaError(
                "cargoquery requires a LiquipediaDB API key. "
                "Apply at https://api.liquipedia.net/ — free tier 60 req/h, "
                "credentials granted on approval. Set LIQUIPEDIA_API_KEY "
                "in .env once you have it."
            )
        # NOTE: The exact base URL, header name (e.g. 'apikey' vs
        # 'Authorization: Bearer ...'), and parameter naming for
        # api.liquipedia.net are documented behind the LiquipediaDB
        # Dashboard login. Wire them up when the user supplies docs.
        raise LiquipediaError(
            "LIQUIPEDIA_API_KEY is set but the LiquipediaDB request "
            "shape isn't wired yet — paste one example curl from the "
            "Dashboard docs and I'll fill it in."
        )
        # Unreachable for now; kept as a reference of what params the
        # MediaWiki Cargo extension expects, since LiquipediaDB is a
        # superset of that contract:
        params: dict[str, Any] = {  # noqa: F841
            "action": "cargoquery",
            "format": "json",
            "tables": tables,
            "fields": fields,
            "limit": limit,
            "offset": offset,
        }
        if where:
            params["where"] = where
        if order_by:
            params["order_by"] = order_by
        if group_by:
            params["group_by"] = group_by
        if join_on:
            params["join_on"] = join_on

        cache_path = self._cache_path(params)
        if use_cache and cache_path.exists():
            log.debug("liquipedia cache hit: %s", cache_path)
            return json.loads(cache_path.read_text())

        self._throttle()
        log.info(
            "event=liquipedia_request action=cargoquery tables=%s limit=%d offset=%d",
            tables, limit, offset,
        )
        try:
            r = self._client.get(API_URL, params=params)
        except httpx.HTTPError as e:
            raise LiquipediaError(f"network error: {e}") from e

        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            raise LiquipediaRateLimited(
                f"Liquipedia 429; Retry-After={retry_after}"
            )
        if r.status_code >= 500:
            raise LiquipediaError(f"Liquipedia upstream {r.status_code}")
        if r.status_code != 200:
            raise LiquipediaError(
                f"Liquipedia status {r.status_code}: {r.text[:200]}"
            )

        try:
            payload = r.json()
        except json.JSONDecodeError as e:
            raise LiquipediaError(f"non-JSON response: {e}") from e

        if "error" in payload:
            raise LiquipediaError(f"Liquipedia API error: {payload['error']}")

        # cargoquery rows live under {"cargoquery": [{"title": {...row...}}, ...]}
        rows = [item.get("title", {}) for item in payload.get("cargoquery", [])]

        if use_cache:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(rows, ensure_ascii=False))

        return rows
