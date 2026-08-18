# rate-limiter

Token-bucket rate limiter implemented as FastAPI middleware, backed by a single
Redis node. Each client gets its own bucket keyed by source IP, with capacity
and refill rate configurable. The check-and-decrement runs as one atomic Lua
script so concurrent requests cannot both spend the same token.

Responses carry `X-RateLimit-Limit` and `X-RateLimit-Remaining`; a rejected
request gets a 429 with `Retry-After`.

## Correctness: oversell under burst load

The reason the logic lives in a Lua script rather than in application code. A
read-then-write limiter lets concurrent requests observe the same balance before
any of them writes it back, so each sees a token available and spends it.

The benchmark fires a burst at a single bucket of capacity 50 with refill
disabled, so a correct limiter allows exactly 50 requests and any extra allow is
unambiguous oversell rather than a token the bucket legitimately earned
mid-burst. Reproduce with `./bench_race.sh`, which runs both implementations via
`RATELIMIT_ALGO=naive|lua`; the script reports the burst rate it actually
achieved so a slow machine cannot quietly test less than it claims.

1,500 simultaneous requests against a bucket of capacity 50, 5 trials each,
burst delivered at a measured 2,948 rps (naive) and 5,609 rps (lua):

| Implementation | Requests allowed | Oversell | Per-trial |
| --- | --- | --- | --- |
| Naive `HGET` then `HSET` | 1500 | +1450 (**+2900%**) | 1450, 1450, 1450, 1450, 1450 |
| Atomic Lua script | 50 | **0%** | 0, 0, 0, 0, 0 |

The naive oversell is not a narrow race window that occasionally loses. Every
request reads a balance of roughly 50 and writes back roughly 49, and the last
write wins, so the counter never actually descends — the bucket is never
observed empty and every request in the burst is allowed. Oversell therefore
scales with offered load rather than being bounded by anything, which is what
makes the read-then-write version unfixable by retrying or by shrinking the gap
between the two calls.

`RATELIMIT_ALGO=naive` exists only to make this measurable and must never be
set in production.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `REDIS_HOST` | `localhost` | Redis to connect to |
| `RATELIMIT_CAPACITY` | `5` | Bucket size — the largest burst one client may spend at once |
| `RATELIMIT_REFILL_RATE` | `2` | Tokens per second, sustained. `0` disables refill (benchmark only) |
| `RATELIMIT_ALGO` | `lua` | `naive` reproduces the pre-fix race. Benchmark only |

## Running it

Locally, app and Redis together:

```
docker compose up --build
```

## Deployment

Containerized and deployed to AWS ECS Fargate, provisioned with Terraform. The
config in `terraform/` covers the ECR repository, ECS cluster, task definition
(app plus a Redis sidecar), task execution role and log policy, security group,
and the service itself — so a full environment comes up from:

```
cd terraform && terraform init && terraform apply
```

Build and push the image to the ECR repository, then the service pulls
`:latest` on next deploy.

## Scope

Single-node Redis. The atomicity guarantee holds for one Redis instance;
multi-node coordination, replica lag, and consensus are not implemented. The
ECS task runs Redis as a sidecar in the same task as the app, so bucket state is
in-memory and shared only within that task.
