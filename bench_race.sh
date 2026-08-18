#!/usr/bin/env bash
# Runs the oversell benchmark once per algorithm.
#
# Each algorithm gets its own uvicorn process because RATELIMIT_ALGO is read at
# import time — flipping it mid-run would not take effect. Refill is disabled so
# the expected allow count is exactly the capacity, making any extra allow
# unambiguous oversell rather than a legitimately earned token.
set -euo pipefail
cd "$(dirname "$0")"
capacity=50
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" == "--capacity" ]]; then
    capacity="${args[i+1]}"
  fi
done
PY=./venv/bin/python

run_algo() {
  local algo=$1
  # Drop the algorithm so any remaining arguments (--clients, --trials) can be
  # forwarded to the benchmark unchanged.
  shift
  # Invoked as a module, not via venv/bin/uvicorn: this venv was built at an
  # older path, so its console-script shebangs point at a python that no
  # longer exists. `python -m` sidesteps them entirely.
  RATELIMIT_ALGO=$algo RATELIMIT_CAPACITY=$capacity RATELIMIT_REFILL_RATE=0 \
    $PY -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
    --log-level warning &
  local pid=$!

  # Wait for the port to accept connections rather than sleeping a fixed amount.
  until $PY -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1',8000)))" 2>/dev/null; do
    sleep 0.2
  done

  $PY bench_race.py "$algo" "$@"

  kill $pid
  wait $pid 2>/dev/null || true
}

echo ""
echo "Oversell under burst load, single bucket, capacity 50, refill disabled"
echo ""
run_algo naive "$@"
run_algo lua "$@"
echo ""
