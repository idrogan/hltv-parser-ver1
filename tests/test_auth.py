"""Auth hardening tests."""
from __future__ import annotations

import importlib
import sys

import pytest
from httpx import ASGITransport, AsyncClient


pytestmark = pytest.mark.asyncio


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health_is_public(fresh_app):
    app = fresh_app(API_TOKEN="abc123", API_REQUIRE_TOKEN="true")
    async with await _client(app) as ac:
        r = await ac.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_warmup_is_public(fresh_app):
    app = fresh_app(API_TOKEN="abc123", API_REQUIRE_TOKEN="true")
    async with await _client(app) as ac:
        r = await ac.get("/warmup")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "warm": True}


async def test_protected_route_rejects_missing_token(fresh_app):
    app = fresh_app(API_TOKEN="abc123", API_REQUIRE_TOKEN="true")
    async with await _client(app) as ac:
        r = await ac.get("/hltv/rankings")
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthorized"}


async def test_protected_route_rejects_wrong_token(fresh_app):
    app = fresh_app(API_TOKEN="abc123", API_REQUIRE_TOKEN="true")
    async with await _client(app) as ac:
        r = await ac.get(
            "/hltv/rankings",
            headers={"Authorization": "Bearer wrong"},
        )
    assert r.status_code == 401
    # Same body as 'missing' — must not leak which case it is.
    assert r.json() == {"detail": "unauthorized"}


async def test_missing_and_wrong_return_identical_response(fresh_app):
    app = fresh_app(API_TOKEN="abc123", API_REQUIRE_TOKEN="true")
    async with await _client(app) as ac:
        r_missing = await ac.get("/hltv/rankings")
        r_wrong = await ac.get(
            "/hltv/rankings", headers={"Authorization": "Bearer wrong"}
        )
    assert r_missing.status_code == r_wrong.status_code
    assert r_missing.json() == r_wrong.json()


async def test_startup_fails_when_required_token_missing(monkeypatch):
    """API_REQUIRE_TOKEN=true with empty API_TOKEN must crash on import."""
    monkeypatch.setenv("API_REQUIRE_TOKEN", "true")
    monkeypatch.delenv("API_TOKEN", raising=False)
    for mod in ("app", "common", "common.middleware"):
        sys.modules.pop(mod, None)
    with pytest.raises(Exception) as exc_info:
        importlib.import_module("app")
    # The custom exception class lives in common.
    assert "API_TOKEN" in str(exc_info.value)


async def test_startup_warns_but_runs_when_token_unset_and_not_required(fresh_app):
    """Local dev mode: no token required, no token set → app boots."""
    # fresh_app sets envs explicitly; pass empty string for API_TOKEN.
    app = fresh_app(API_TOKEN="", API_REQUIRE_TOKEN="false")
    async with await _client(app) as ac:
        r = await ac.get("/health")
    assert r.status_code == 200
