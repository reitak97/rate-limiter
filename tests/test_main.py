from fastapi.testclient import TestClient
from app.main import app
import pytest, redis



@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
        
@pytest.fixture(autouse=True)
def reset_redis():
    r = redis.Redis(host="localhost", port=6379, decode_responses=True)
    r.delete("user:testclient")
    r.close()

def test_generate_success(client):
    response = client.post("/generate", json={"text": "Hello, world!"})
    assert response.status_code == 200
    assert "X-RateLimit-Remaining" in response.headers
    assert int(response.headers["X-RateLimit-Remaining"]) >= 0

def test_rate_limit_exceeded(client):
    for _ in range(5):
        client.post("/generate", json={"text": "hello"})
    response = client.post("/generate", json={"text": "hello"})
    assert response.status_code == 429
    assert "Retry-After" in response.headers


