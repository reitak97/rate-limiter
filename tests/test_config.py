# Covers the configuration switches. These are read at import time, so each test
# reloads the module with a different environment rather than mutating a live app.
import importlib

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
