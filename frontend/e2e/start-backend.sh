#!/bin/sh
# A fresh, seeded, keyless backend for the local end-to-end project: its own SQLite file, offline prices.
set -eu
cd "$(dirname "$0")/../../backend"
tmp="${TMPDIR:-/tmp}"
tmp="${tmp%/}"
export DATABASE_URL="sqlite:///${tmp}/countersign-e2e-$$.sqlite3"
export COUNTERSIGN_LIVE_PRICES=false
# Every test creates its own workspace; lift the per-address hourly limit for repeated local runs.
export COUNTERSIGN_RATE_LIMIT_WORKSPACES_PER_HOUR=1000
export COUNTERSIGN_RATE_LIMIT_RUNS_PER_HOUR=1000
uv sync --quiet
uv run countersign seed
exec uv run uvicorn main:app --host 127.0.0.1 --port 8000
