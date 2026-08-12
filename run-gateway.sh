#!/usr/bin/env bash
# Start the Omnirouter gateway using the local .env for provider keys.
# Usage: ./run-gateway.sh        (reads GATEWAY_PORT from .env, default 8787)
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "No .env found. Copy .env.example to .env and add your keys." >&2
  exit 1
fi

PORT="${GATEWAY_PORT:-8787}"
echo "Starting Omnirouter gateway on http://127.0.0.1:${PORT}"
exec python3 -m uvicorn gateway.main:app --env-file .env --host 127.0.0.1 --port "${PORT}"
