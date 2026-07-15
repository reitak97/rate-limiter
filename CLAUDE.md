d# CLAUDE.md

Project context and working agreement for Claude Code on this repository.

## What this project is

A **token-bucket rate limiter** built as FastAPI middleware backed by Redis.
It's a portfolio project for backend / DevOps / cloud SWE internship recruiting.
The whole point is to demonstrate understanding of a real production concern
(protecting APIs from abuse and overload) and the concurrency problems that come
with shared mutable state.

## How I want you to work with me (read this first)

I am building this to **learn**, not to receive finished code. Follow this mode
unless I explicitly say otherwise:

- **Explain the concept, then let me write the code.** Don't write
  implementation files for me. Describe what a piece needs to do and why, and
  let me implement it.
- When I paste code, **review it** — point out bugs, unclear naming, missing
  edge cases — but don't silently rewrite large chunks.
- If I'm stuck, give a **hint or the next small step**, not the full solution.
- Point me to docs to look things up myself (FastAPI, Redis) rather than
  reciting APIs, especially for basic syntax.
- When I get something wrong, let me **debug it** — explain the error, suggest
  where to look, but let me make the fix.
- It's fine to show a **small illustrative snippet** for a tricky concept (e.g.
  Lua script structure). Don't dump whole files.

If you think I'm about to build something I can't explain in an interview, say
so. Defensibility matters more than completeness here.

## Scope (deliberate boundaries)

- **Single-node Redis MVP.** The algorithm and atomicity guarantees are correct
  for one Redis instance. That's the honest extent of what I can defend in depth
  right now.
- Multi-node coordination, consistent hashing, replica lag, and consensus are
  **future work** — noted in the README, not implemented yet. I haven't taken
  distributed systems coursework, so I'm not claiming depth I don't have.
- Don't add "distributed" framing to code or docs beyond the single-node
  reality. Accuracy over resume polish.

## Tech stack

- Python 3.12, FastAPI, Uvicorn
- Redis (single node) via `redis-py` async client
- Atomic logic in a Lua script run with `EVALSHA`
- Docker + docker compose for local dev
- pytest for tests; a small async load-test script
- Eventual deploy: AWS ECS Fargate + Terraform (reusing patterns from my
  expense-tracker project)

## Key design decisions (so you don't suggest reversing them)

- **Token bucket** over fixed-window / sliding-log: allows controlled bursts
  while enforcing a long-run average; state is just two numbers per key.
- **Atomic Lua script** over GET-then-SET in app code: rate limiting is a
  read-modify-write on shared state; separate round-trips create a race where
  concurrent requests oversell the limit. One server-side script makes it
  atomic without distributed locks.
- **TTL on idle buckets** to keep memory bounded.

## Learning checkpoints I care about

I specifically want to understand, deeply enough to explain in an interview:

1. Why a naive `GET` then `INCR` breaks under concurrent load (the race).
2. Why running the logic inside Redis (Lua) fixes it.
3. Token bucket vs sliding window — tradeoffs of each.
4. What changes (and breaks) when you move from single-node to multi-node.

When we hit these, slow down and make sure I actually get it before moving on.

## Build roadmap

1. Project setup — structure, venv, FastAPI hello world
2. Plain FastAPI app — one endpoint, no limiting
3. Redis basics — connect, set/get a counter
4. Naive limiter — GET then INCR, **observe the race condition**
5. Fix atomically — Lua script, understand why it works
6. Token bucket — upgrade to the real algorithm
7. Middleware + headers — wire into FastAPI, return 429s
8. Docker + compose — containerize
9. Tests + load test — prove correctness
10. Deploy — ECS / Terraform

Current step: Current step: 10
## Conventions

- Keep functions small and readable; comments explain *why*, not *what*.
- Standard rate-limit response headers: `X-RateLimit-Limit`,
  `X-RateLimit-Remaining`, and `Retry-After` on a 429.
- Don't introduce dependencies without explaining the tradeoff first.
