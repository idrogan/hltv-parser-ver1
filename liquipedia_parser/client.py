"""Liquipedia API client.

Path chosen: public MediaWiki ``action=query`` against
https://liquipedia.net/counterstrike/api.php — no API key.

Liquipedia support confirmed (2026-05-14) that for this project the
public site is sufficient: data is available, an API key is not
required. Practical consequence: we read wikitext via ``action=query``
and parse it locally (see ``wikitext.py``), instead of using the
heavily-throttled ``action=parse`` (1 req / 30 sec) or the gated
LiquipediaDB API (60 req / hour, requires approval).

TOS reference (https://liquipedia.net/api-terms-of-use, fetched
2026-05-14):

  1. Rate limit ALL HTTP requests to no more than 1 request per 2 seconds.
  2. action=parse must not exceed 1 / 30 seconds  → we avoid it.
  3. User-Agent: ``ProjectName/version (https://example.com/; you@example.com)``
     — Generic UAs (``python-requests``, ``Go-http-client``, ...) are
     actively blocked. Email is mandatory.
  4. HTTP client must accept Content-Encoding: gzip.
  5. Reuse the HTTP client across requests.
  6. Violations → automated temporary IP bans.
  7. LiquipediaDB API: ≤ 60 req/hour, credentials issued on approval —
     not used here; kept as a stub in ``cargoquery`` for future need.

Plus our own additions:

  * Filesystem cache keyed by query + UTC day so re-runs in the same
    day don't re-fetch.
  * Hard floor of 2.0 s between requests, enforced under a lock so
    concurrent callers can't accidentally violate TOS.
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

    def _query(
        self,
        params: dict[str, Any],
        *,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Issue one MediaWiki ``api.php`` request with TOS throttling.

        Returns the parsed JSON payload. Caller is responsible for
        digging into ``payload["query"]``.
        """
        params = {**params, "format": "json", "formatversion": "2"}

        cache_path = self._cache_path(params)
        if use_cache and cache_path.exists():
            log.debug("liquipedia cache hit: %s", cache_path)
            return json.loads(cache_path.read_text())

        self._throttle()
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

        if use_cache:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False))

        return payload

    def query_revisions(
        self,
        title: str,
        *,
        use_cache: bool = True,
    ) -> Optional[str]:
        """Fetch raw wikitext for one page via ``action=query``.

        Returns the wikitext string, or ``None`` if the page is missing
        / redirected to nothing. We follow redirects so e.g.
        ``PGL_Major_Copenhagen_2024`` is normalised to its canonical
        title.

        Uses the standard 1 req / 2 sec throttle (not the 1 / 30 sec
        ``action=parse`` rate), which is why we read raw wikitext and
        parse it locally.
        """
        log.info("event=liquipedia_request action=query.revisions title=%r", title)
        payload = self._query(
            {
                "action": "query",
                "prop": "revisions",
                "titles": title,
                "rvslots": "main",
                "rvprop": "content",
                "redirects": "1",
            },
            use_cache=use_cache,
        )
        pages = payload.get("query", {}).get("pages", []) or []
        if not pages:
            return None
        page = pages[0]
        if page.get("missing") or page.get("invalid"):
            return None
        revisions = page.get("revisions") or []
        if not revisions:
            return None
        slots = revisions[0].get("slots") or {}
        main = slots.get("main") or {}
        return main.get("content")

    def category_members(
        self,
        category: str,
        *,
        limit_per_page: int = 500,
        max_pages: int = 5,
        use_cache: bool = True,
    ) -> list[str]:
        """List page titles in a Liquipedia category.

        ``category`` is the title without the ``Category:`` prefix
        (e.g. ``"S-Tier_Tournaments"``). Pagination uses MediaWiki's
        ``cmcontinue`` token; we cap at ``max_pages`` continuations to
        keep a single discovery call bounded under TOS.
        """
        log.info(
            "event=liquipedia_request action=query.categorymembers cat=%r",
            category,
        )
        titles: list[str] = []
        cont: Optional[str] = None
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": f"Category:{category}",
                "cmlimit": limit_per_page,
                "cmtype": "page",
            }
            if cont:
                params["cmcontinue"] = cont
            payload = self._query(params, use_cache=use_cache)
            members = payload.get("query", {}).get("categorymembers", []) or []
            titles.extend(m.get("title") for m in members if m.get("title"))
            cont = (payload.get("continue") or {}).get("cmcontinue")
            if not cont:
                break
        return titles

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
