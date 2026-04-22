"""Rate-limit + real-IP-resolution tests."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


pytestmark = pytest.mark.asyncio


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_rate_limit_returns_429_after_burst(fresh_app):
    """With limit=2/min, the third request from the same IP is 429'd."""
    app = fresh_app(
        API_TOKEN="t",
        API_REQUIRE_TOKEN="true",
        RATE_LIMIT_PER_MINUTE="2",
    )
    headers = {"X-Forwarded-For": "203.0.113.7"}
    async with await _client(app) as ac:
        r1 = await ac.get("/health", headers=headers)
        r2 = await ac.get("/health", headers=headers)
        r3 = await ac.get("/health", headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    # slowapi's default body shape: {"error": "Rate limit exceeded: N per 1 minute"}
    assert "rate limit" in r3.json()["error"].lower()


async def test_separate_ips_have_separate_buckets(fresh_app):
    """Render-style proxying: X-Forwarded-For determines the bucket."""
    app = fresh_app(
        API_TOKEN="t",
        API_REQUIRE_TOKEN="true",
        RATE_LIMIT_PER_MINUTE="1",
    )
    async with await _client(app) as ac:
        r_a = await ac.get("/health", headers={"X-Forwarded-For": "203.0.113.1"})
        r_b = await ac.get("/health", headers={"X-Forwarded-For": "203.0.113.2"})
    assert r_a.status_code == 200
    assert r_b.status_code == 200  # different IP, fresh bucket


async def test_xff_uses_leftmost_hop(fresh_app):
    """When XFF has multiple hops we attribute the request to the original client."""
    app = fresh_app(
        API_TOKEN="t",
        API_REQUIRE_TOKEN="true",
        RATE_LIMIT_PER_MINUTE="1",
    )
    async with await _client(app) as ac:
        # Same original client (.7), different proxy chain — should share a bucket.
        r1 = await ac.get(
            "/health", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
        )
        r2 = await ac.get(
            "/health",
            headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.2, 10.0.0.3"},
        )
    assert r1.status_code == 200
    assert r2.status_code == 429
