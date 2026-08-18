"""Measures limit oversell under burst load.

Fires N simultaneous requests at a single bucket of known capacity and counts
how many were allowed. A correct limiter allows exactly `capacity`. Anything
above that is oversell: requests that were let through because two of them read
the same balance before either wrote it back.

The server must run with RATELIMIT_REFILL_RATE=0 so the expected allow count is
exactly `capacity` for the whole burst. With refill on, a burst that straddles a
one-second boundary legitimately earns tokens mid-flight and the extra allows
would be indistinguishable from oversell.

Run against RATELIMIT_ALGO=naive and RATELIMIT_ALGO=lua to compare.
"""

import argparse
import asyncio
import time

import aiohttp
import redis

URL = "http://127.0.0.1:8000/generate"
PAYLOAD = {"text": "hello"}
BUCKET_KEY = "user:127.0.0.1"


def reset(capacity):
    """Start every trial from a full bucket with a fresh refill timestamp."""
    r = redis.Redis(decode_responses=True) 
    now = r.time()[0]
    r.hset(BUCKET_KEY, mapping={"tokens": capacity, "last_refill": now})
    r.close()


async def burst(session, count):
    """Send `count` requests at once and return their statuses and duration."""
    async def one():
        async with session.post(URL, json=PAYLOAD) as resp:
            await resp.read()
            return resp.status

    started = time.perf_counter()
    statuses = await asyncio.gather(*[one() for _ in range(count)])
    return statuses, time.perf_counter() - started


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--clients", type=int, default=1500)
    parser.add_argument("--capacity", type=int, default=50)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()

    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Warm the connection pools first. A cold pool serialises the burst,
        # which would hide the very interleaving this test exists to expose.
        reset(args.capacity)
        await burst(session, args.clients)

        oversells = []
        rates = []
        for trial in range(args.trials):
            reset(args.capacity)
            statuses, elapsed = await burst(session, args.clients)
            allowed = sum(1 for s in statuses if s == 200)
            oversells.append(allowed - args.capacity)
            rates.append(args.clients / elapsed)

        worst = max(oversells)
        pct = 100.0 * worst / args.capacity
        detail = ", ".join(str(o) for o in oversells)
        # The achieved rate is what makes the burst a burst; report it rather
        # than assuming it, since a slower machine would quietly test less.
        print(f"  {args.label:<6} clients={args.clients} capacity={args.capacity} "
              f"min_rate={min(rates):.0f} rps  "
              f"oversell per trial: [{detail}]  worst=+{worst} ({pct:+.0f}%)")


if __name__ == "__main__":
    asyncio.run(main())
