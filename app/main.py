
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from pydantic import BaseModel
import redis.asyncio as redis
import os
from contextlib import asynccontextmanager
script = """
    local tokens = redis.call('HGET', KEYS[1], 'tokens')
    local last_refill = redis.call('HGET', KEYS[1], 'last_refill')

    if tokens == false then
        tokens = 10
    end

    if last_refill == false then
        last_refill = redis.call('TIME')[1]
    end



    local elapsed = redis.call('TIME')[1] - tonumber(last_refill)
    tokens = math.min(elapsed * 2 + tonumber(tokens), tonumber(ARGV[1]))


    if tokens < 1 then
        return -1
    end 

    tokens = tokens - 1
    redis.call('HSET', KEYS[1], 'tokens', tokens, 'last_refill', redis.call('TIME')[1])
    return tokens

"""

class Prompt(BaseModel):
    text: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, decode_responses=True
    )
    yield
    await app.state.redis.aclose()

app = FastAPI(lifespan=lifespan)


@app.post("/generate")
async def generate(prompt: Prompt):
    return {"response": f"AI response to: {prompt.text}"}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Add any necessary middleware logic here
    client_id = request.client.host
    result = await request.app.state.redis.eval(script, 1, f"user:{client_id}", 5)
    if result == -1:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"},
        headers={"Retry-After": "1"})
    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(result)
    response.headers["X-RateLimit-Limit"] = "5"
    return response