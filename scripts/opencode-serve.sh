#!/usr/bin/env bash
# Idempotently ensure a local `opencode serve` instance is running so the
# gateway's opencode backend (claude-opencode-* models) has something to talk
# to. opencode serve is session-based; the gateway bridges Anthropic <-> it.
#
# The port must match `opencode_serve_url` in gateway_config.yaml (default 5051;
# 5001-5003 are avoided because the Datadog agent squats them on some hosts).
# Always exits 0 so it never blocks a session hook.
set -uo pipefail

PORT="${OPENCODE_SERVE_PORT:-5051}"
HOST="127.0.0.1"
URL="http://${HOST}:${PORT}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$DIR/.opencode-serve.log"

# opencode is installed under ~/.opencode/bin on this host.
export PATH="$HOME/.opencode/bin:$PATH"

if ! command -v opencode >/dev/null 2>&1; then
  echo "omnirouter: opencode CLI not found on PATH; skipping" >&2
  exit 0
fi

# Already up? (opencode serve answers 200 on /config once ready.)
if curl -s -o /dev/null --max-time 1 "$URL/config"; then
  exit 0
fi

cd "$DIR"
nohup opencode serve --hostname "$HOST" --port "$PORT" >> "$LOG" 2>&1 &
disown 2>/dev/null || true

# opencode serve is slow to bind (~10-15s cold); cap ~30s.
for _ in $(seq 1 60); do
  if curl -s -o /dev/null --max-time 1 "$URL/config"; then
    echo "omnirouter: opencode serve up on $URL" >&2
    exit 0
  fi
  sleep 0.5
done

echo "omnirouter: opencode serve did not come up in time (see $LOG)" >&2
exit 0
