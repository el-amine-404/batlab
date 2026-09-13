#!/usr/bin/env bash

set -Eeuo pipefail

readonly WATCHDOG_CONFIG_FILE="${WATCHDOG_CONFIG_FILE:-/etc/batlab-watchdog/watchdog.env}"

load_watchdog_config() {
  if [[ ! -r "$WATCHDOG_CONFIG_FILE" ]]; then
    echo "Cannot read $WATCHDOG_CONFIG_FILE" >&2
    exit 1
  fi

  set -a
  # shellcheck source=/dev/null
  source "$WATCHDOG_CONFIG_FILE"
  set +a

  : "${WATCHDOG_DATA_ROOT:?WATCHDOG_DATA_ROOT is not configured}"
}

# Notifications are best-effort: a guard that stopped containers must not fail
# because Discord was unreachable.
watchdog_notify() {
  local title="$1" body="$2" color="$3"

  [[ -n "${WATCHDOG_DISCORD_WEBHOOK:-}" ]] || return 0

  local payload
  payload="$(python3 -c '
import json, socket, sys
print(json.dumps({
    "username": "watchdog on " + socket.gethostname(),
    "embeds": [{"title": sys.argv[1], "description": sys.argv[2][:3500], "color": int(sys.argv[3])}],
}))' "$title" "$body" "$color")"
  curl -sS -m 15 -H "Content-Type: application/json" -d "$payload" \
    "$WATCHDOG_DISCORD_WEBHOOK" >/dev/null 2>&1 || true
}
