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
#   watchdog_notify TITLE COLOR [FIELD_NAME FIELD_VALUE]...
watchdog_notify() {
  local title="$1" color="$2"
  shift 2

  [[ -n "${WATCHDOG_DISCORD_WEBHOOK:-}" ]] || return 0

  local payload
  payload="$(python3 -c '
import datetime, json, socket, sys
title, color, pairs = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
host = socket.gethostname()
fields = [{"name": "Host", "value": host, "inline": True}]
fields += [{"name": n, "value": v[:1000] or "-", "inline": False} for n, v in zip(pairs[::2], pairs[1::2])]
print(json.dumps({
    "username": "watchdog on " + host,
    "avatar_url": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/debian-linux.png",
    "embeds": [{
        "title": title,
        "color": color,
        "fields": fields,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }],
}))' "$title" "$color" "$@")"
  curl -sS -m 15 -H "Content-Type: application/json" -d "$payload" \
    "$WATCHDOG_DISCORD_WEBHOOK" >/dev/null 2>&1 || true
}
