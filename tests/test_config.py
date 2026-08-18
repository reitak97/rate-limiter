# Covers the configuration switches. These are read at import time, so each test
# reloads the module with a different environment rather than mutating a live app.
import asyncio
import importlib

import httpx
import pytest
from fastapi.testclient import TestClient
import redis

import app.main


def load(monkeypatch, **env):
    """Re-import app.main with the given environment applied."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(app.main)


@pytest.fixture(autouse=True)
def restore():
    # Clear the bucket so a test's own requests start from a full bucket, and
    # leave the module in its default state so later tests (and other files)
    # see the production configuration.
    r = redis.Redis(decode_responses=True)
    r.delete("user:testclient")
    r.close()
    yield
    importlib.reload(app.main)


# An unrecognised algorithm must fail at import rather than silently defaulting
# to "lua" — a typo in the benchmark script would otherwise produce numbers that
# look valid but measured the wrong implementation.
def test_invalid_algo_raises(monkeypatch):
    with pytest.raises(ValueError, match="RATELIMIT_ALGO"):
        load(monkeypatch, RATELIMIT_ALGO="atomic")


# A raised capacity must actually be honoured, including for a client Redis has
# never seen before — that first-request path reads capacity from ARGV, so a
# hardcoded default there would silently ignore the override.
def test_capacity_override_is_applied(monkeypatch):
    module = load(monkeypatch, RATELIMIT_CAPACITY="50")
    with TestClient(module.app) as client:
        statuses = [client.post("/generate", json={"text": "hi"}).status_code
                    for _ in range(10)]
    assert statuses == [200] * 10


# Refill disabled is what makes the oversell benchmark unambiguous: the bucket
# must allow exactly capacity requests and never earn a token back mid-run.
def test_zero_refill_allows_exactly_capacity(monkeypatch):
    module = load(monkeypatch, RATELIMIT_CAPACITY="3", RATELIMIT_REFILL_RATE="0")
    with TestClient(module.app) as client:
        statuses = [client.post("/generate", json={"text": "hi"}).status_code
                    for _ in range(6)]
    assert statuses == [200, 200, 200, 429, 429, 429]


# spend_token_naive is exercised only by bench_race.py, a script nothing runs
# automatically. Without this, a refactor could silently fix or break its race
# and every test would still pass — the naive-vs-lua benchmark would quietly
# become lua-vs-lua. Firing requests one at a time (like the test above) can't
# reproduce it: the race needs two requests' HGET/HSET to interleave, which
# requires them in flight at once, not sequenced.
def test_naive_algo_oversells_under_concurrent_load(monkeypatch):
    capacity = 5
    module = load(
        monkeypatch,
        RATELIMIT_ALGO="naive",
        RATELIMIT_CAPACITY=str(capacity),
        RATELIMIT_REFILL_RATE="0",
    )

    async def burst():
        # TestClient's own request loop is synchronous; only a real async
        # client sending requests concurrently can trigger the interleave.
        async with module.app.router.lifespan_context(module.app):
            transport = httpx.ASGITransport(app=module.app, client=("testclient", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                return await asyncio.gather(
                    *[ac.post("/generate", json={"text": "hi"}) for _ in range(50)]
                )

    responses = asyncio.run(burst())
    allowed = sum(1 for r in responses if r.status_code == 200)
    assert allowed > capacity
