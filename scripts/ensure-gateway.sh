#!/usr/bin/env bash
# Idempotent gateway launcher for Claude Code SessionStart.
# Starts the Omnirouter gateway only if it is not already listening, then waits
# briefly until the port is accepting connections so the session's first request
# does not hit a dead base URL. Never fails the hook (always exits 0).
set -uo pipefail

ROOT="/Users/gustavoschneiter/Documents/Omnirouter"
PORT="${GATEWAY_PORT:-8787}"

listening() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; }

if ! listening; then
  cd "$ROOT" || exit 0
  PY="$ROOT/.venv/bin/python"
  [ -x "$PY" ] || PY="python3"
  nohup "$PY" -m uvicorn gateway.main:app --env-file .env \
    --host 127.0.0.1 --port "$PORT" >> "$ROOT/.gateway.log" 2>&1 &
  # Wait up to ~6s for boot so the session doesn't race a cold gateway.
  for _ in $(seq 1 20); do
    listening && break
    sleep 0.3
  done
fi

exit 0
