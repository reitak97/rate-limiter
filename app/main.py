
# FastAPI creates the web application; Request exposes details of each HTTP request.
from fastapi import FastAPI, Request
# JSONResponse lets the middleware return a custom JSON error response.
from starlette.responses import JSONResponse
# BaseModel validates JSON request bodies against a declared shape.
from pydantic import BaseModel
# The asyncio Redis client allows Redis calls without blocking other requests.
import redis.asyncio as redis
# os reads configuration such as REDIS_HOST from environment variables.
import os
# This decorator turns an async generator into FastAPI startup/shutdown logic.
from contextlib import asynccontextmanager

# Lua runs inside Redis, so this whole check/update is atomic: concurrent requests
# cannot both spend the same token.
script = """
    -- Read this client's currently stored token count from its Redis hash.
    local tokens = redis.call('HGET', KEYS[1], 'tokens')
    -- Read the Unix timestamp (seconds) of the last refill for this client.
    local last_refill = redis.call('HGET', KEYS[1], 'last_refill')

    -- A missing value means this is a client Redis has not seen before.
    if tokens == false then
        -- A new client starts with a full bucket. This must be the configured
        -- capacity, not a literal: hardcoding it meant a raised capacity was
        -- silently ignored for every first-time client.
        tokens = tonumber(ARGV[1])
    end

    -- On a client's first request, use Redis's current server time as the
    -- starting point, so no refill time has elapsed yet.
    if last_refill == false then
        last_refill = redis.call('TIME')[1]
    end

    -- Measure elapsed whole seconds using Redis's clock, avoiding clock
    -- differences between application servers.
    local elapsed = redis.call('TIME')[1] - tonumber(last_refill)
    -- Refill ARGV[2] tokens per elapsed second, then cap the bucket at ARGV[1]
    -- (the capacity passed by Python). tonumber converts Redis strings to numbers.
    tokens = math.min(elapsed * tonumber(ARGV[2]) + tonumber(tokens), tonumber(ARGV[1]))

    -- If no complete token remains, reject this request. Python treats -1 as
    -- the "rate limited" signal.
    if tokens < 1 then
        return -1
    end

    -- Spend exactly one token to allow this request.
    tokens = tokens - 1
    -- Persist both the new balance and the time of this refill calculation in
    -- one Redis hash write. KEYS[1] is the per-client key supplied by Python.
    redis.call('HSET', KEYS[1], 'tokens', tokens, 'last_refill', redis.call('TIME')[1])
    -- Return the remaining balance to Python for the response header.
    return tokens

"""

# Bucket capacity: the largest burst a single client may spend at once.
CAPACITY = int(os.getenv("RATELIMIT_CAPACITY", "5"))

# Sustained tokens per second once the burst allowance is spent. Configurable so
# the oversell benchmark can set it to 0: with refill disabled, a bucket can
# allow exactly CAPACITY requests and no more, so any extra allow is unambiguous
# oversell rather than a token the bucket legitimately earned mid-burst.
REFILL_RATE = float(os.getenv("RATELIMIT_REFILL_RATE", "2"))

# "lua" is the real implementation. "naive" reproduces the pre-fix read-then-write
# version and exists only so the race it suffers can be measured rather than
# asserted. Never set this outside a benchmark.
RATELIMIT_ALGO = os.getenv("RATELIMIT_ALGO", "lua")
if RATELIMIT_ALGO not in ("lua", "naive"):
    raise ValueError(f"RATELIMIT_ALGO must be lua|naive, got {RATELIMIT_ALGO!r}")


async def spend_token_naive(client, key, capacity):
    """Read, decide, then write — as separate round-trips.

    Two requests can both read the same balance before either writes, so both
    see a token available and both spend it. The limit is oversold by however
    many requests interleave inside that window.
    """
    raw = await client.hget(key, "tokens")
    tokens = capacity if raw is None else int(raw)
    if tokens < 1:
        return -1
    tokens -= 1
    await client.hset(key, "tokens", tokens)
    return tokens

# This says a valid POST body must look like: {"text": "..."}.
class Prompt(BaseModel):
    text: str

# Run this function once when FastAPI starts, and resume it after shutdown.
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create one async Redis client and store it on the shared app state.
    app.state.redis = redis.Redis(
        # Use REDIS_HOST when configured; otherwise assume Redis is local.
        host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, decode_responses=True
    )
    # FastAPI starts accepting requests while execution is paused here.
    yield
    # When the server shuts down, close Redis connections cleanly.
    await app.state.redis.aclose()

# Register the lifespan hooks while creating the FastAPI application.
app = FastAPI(lifespan=lifespan)


# Route POST /generate requests to this function.
@app.post("/generate")
async def generate(prompt: Prompt):
    # `prompt` has already been parsed and validated as a Prompt instance.
    return {"response": f"AI response to: {prompt.text}"}


async def rate_limit_middleware(request: Request, call_next):
    # Group requests by source IP address. All clients behind one NAT/proxy may
    # therefore share a bucket unless trusted proxy handling is added.
    client_id = request.client.host
    # Evaluate the Lua program in Redis:
    # - `1` means the next argument is one Redis key (KEYS[1]);
    # - `user:<IP>` is that key; and
    # - capacity and refill rate follow as ARGV[1] and ARGV[2].
    # The whole read-modify-write happens inside Redis, so two concurrent
    # requests can never both spend the same token.
    if RATELIMIT_ALGO == "naive":
        result = await spend_token_naive(
            request.app.state.redis, f"user:{client_id}", CAPACITY)
    else:
        result = await request.app.state.redis.eval(
            script, 1, f"user:{client_id}", CAPACITY, REFILL_RATE)
    # The Lua script returns -1 when it could not spend a token.
    if result == -1:
        # End the request early with HTTP 429; the endpoint will not run.
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers={"Retry-After": "1"},
        )
    # A token was available, so hand the request to the matching route handler.
    response = await call_next(request)
    # Tell an allowed client how many tokens remain after this request.
    response.headers["X-RateLimit-Remaining"] = str(result)
    # Document the configured bucket capacity in the response.
    response.headers["X-RateLimit-Limit"] = str(CAPACITY)
    # Return either the endpoint's normal response or the response produced by
    # another middleware further inside the chain.
    return response


app.middleware("http")(rate_limit_middleware)
