#!/usr/bin/env bash
# Idempotently ensure the Omnirouter gateway is running.
# Wired to Claude Code's SessionStart hook: runs every time the IDE opens a
# session, but only starts a detached server when the port is not already up.
# Always exits 0 so it never blocks the session.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${GATEWAY_PORT:-8787}"
URL="http://127.0.0.1:${PORT}/"

# Already running? Nothing to do.
if curl -s -o /dev/null --max-time 1 "$URL"; then
  exit 0
fi

if [[ ! -f "$DIR/.env" ]]; then
  echo "omnirouter: no .env found, skipping gateway autostart" >&2
  exit 0
fi

LOG="$DIR/.gateway.log"
cd "$DIR"
# Detach so the gateway outlives this hook and the session that spawned it.
nohup python3 -m uvicorn gateway.main:app --env-file .env \
  --host 127.0.0.1 --port "$PORT" >> "$LOG" 2>&1 &
disown 2>/dev/null || true

# Wait briefly for it to accept connections (cap ~15s).
for _ in $(seq 1 30); do
  if curl -s -o /dev/null --max-time 1 "$URL"; then
    echo "omnirouter: gateway up on $URL" >&2
    exit 0
  fi
  sleep 0.5
done

echo "omnirouter: gateway did not come up in time (see $LOG)" >&2
exit 0
