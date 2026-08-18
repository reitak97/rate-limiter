# TestClient sends requests to FastAPI without starting a real HTTP server.
from fastapi.testclient import TestClient
# Import the same FastAPI application that production runs.
from app.main import app, CAPACITY
# pytest provides fixtures; redis is used to reset test data before each test.
import pytest, redis


# A fixture creates reusable setup for tests that include `client` as a parameter.
@pytest.fixture
def client():
    # Entering this context starts the app lifespan, including its Redis client.
    with TestClient(app) as c:
        # Give the created test client to the test function.
        yield c

# `autouse=True` runs this fixture before every test, even if the test does not
# explicitly list `reset_redis` as a parameter.
@pytest.fixture(autouse=True)
def reset_redis():
    # Use the synchronous Redis client because this small setup operation is not
    # part of FastAPI's asynchronous request handling.
    r = redis.Redis(host="localhost", port=6379, decode_responses=True)
    # TestClient identifies its requests as host `testclient`, so this is the
    # same key the rate-limit middleware creates: `user:<client_id>`.
    # Deleting it gives every test a fresh, full token bucket.
    r.delete("user:testclient")
    # Close this short-lived setup connection once the reset is finished.
    r.close()

# Verify that one valid request reaches the endpoint and receives rate-limit
# information instead of being rejected.
def test_generate_success(client):
    # Send a JSON request body matching the Prompt model: {"text": string}.
    response = client.post("/generate", json={"text": "Hello, world!"})
    # A permitted request should receive the endpoint's normal success response.
    assert response.status_code == 200
    # The middleware should expose the post-request token balance in this header.
    assert "X-RateLimit-Remaining" in response.headers

    # It should report the configured capacity, not just be present.
    assert response.headers["X-RateLimit-Limit"] == str(CAPACITY)
    # Convert the string HTTP header to a number and ensure it is a valid balance.
    assert int(response.headers["X-RateLimit-Remaining"]) >= 0

# Verify that requests exceeding the bucket capacity are stopped by middleware.
def test_rate_limit_exceeded(client):
    # The bucket starts with five tokens, and each permitted request spends one.
    for _ in range(5):
        client.post("/generate", json={"text": "hello"})
    # This sixth immediate request should have no token available to spend.
    response = client.post("/generate", json={"text": "hello"})
    # HTTP 429 is the standard response for a rate-limited request.
    assert response.status_code == 429
    # The middleware should also tell the client when it may safely retry, and
    # give it the same rate-limit context a successful response would.
    assert response.headers["Retry-After"] == "1"
    assert response.headers["X-RateLimit-Limit"] == str(CAPACITY)
    assert response.headers["X-RateLimit-Remaining"] == "0"

