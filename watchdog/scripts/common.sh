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

  # Empty on a host without a data disk: the heartbeat then skips the disk check.
  : "${WATCHDOG_DATA_ROOT?WATCHDOG_DATA_ROOT is not configured}"
}

# Notifications are best-effort: a guard that stopped containers must not fail
# because a message didn't go out. They go through Apprise as "watchdog on
# <host>" (lib/batlab-common, senders.json) to the alerts channel, and straight
# to WATCHDOG_DISCORD_WEBHOOK when Apprise is down, which is often exactly when
# the watchdog has something to say.
#   watchdog_notify TITLE KIND [FIELD_NAME FIELD_VALUE]...   (KIND: info, success, warning, failure)
watchdog_notify() {
  local title="$1" kind="$2" fields=()
  shift 2
  while (($#)); do
    fields+=(--field "$1" "${2-}")
    shift
    (($#)) && shift
  done
  BATLAB_NOTIFY_FALLBACK="${WATCHDOG_DISCORD_WEBHOOK:-}" python3 -I \
    "$(dirname -- "${BASH_SOURCE[0]}")/../../lib/batlab-common/batlab_common/notify.py" \
    watchdog alerts "$kind" "$title" "${fields[@]}" >/dev/null 2>&1 || true
}
