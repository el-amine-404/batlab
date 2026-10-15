#!/usr/bin/env bash

set -Eeuo pipefail

readonly MEDIASCAN_CONFIG_FILE="${MEDIASCAN_CONFIG_FILE:-/etc/batlab-mediascan/mediascan.env}"

load_mediascan_config() {
  if [[ ! -r "$MEDIASCAN_CONFIG_FILE" ]]; then
    echo "Cannot read $MEDIASCAN_CONFIG_FILE" >&2
    exit 1
  fi

  set -a
  # shellcheck source=/dev/null
  source "$MEDIASCAN_CONFIG_FILE"
  set +a

  : "${MEDIASCAN_QUARANTINE:?MEDIASCAN_QUARANTINE is not configured}"
  : "${MEDIASCAN_LIBRARY_ROOT:?MEDIASCAN_LIBRARY_ROOT is not configured}"

  MEDIASCAN_STATE_DIR="${MEDIASCAN_STATE_DIR:-/var/lib/batlab-mediascan}"
  MEDIASCAN_REPORT_DIR="${MEDIASCAN_REPORT_DIR:-$MEDIASCAN_STATE_DIR/reports}"

  install -d -m 750 "$MEDIASCAN_STATE_DIR" "$MEDIASCAN_REPORT_DIR"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 1
  }
}

read_path_list() {
  local file="$1"

  if [[ ! -r "$file" ]]; then
    echo "Cannot read path list: $file" >&2
    exit 1
  fi

  local path
  while IFS= read -r path; do
    [[ -z "$path" || "$path" == \#* ]] && continue
    if [[ -e "$path" ]]; then
      printf '%s\n' "$path"
    else
      echo "Skipping missing path: $path" >&2
    fi
  done <"$file"
}

read_exclude_list() {
  local file="$1"

  [[ -r "$file" ]] || return 0

  local path
  while IFS= read -r path; do
    [[ -z "$path" || "$path" == \#* ]] && continue
    printf -- '--exclude\n%s\n' "$path"
  done <"$file"
}

# Notifications are best-effort: a scan that found something must not fail
# because a message didn't go out. They go through Apprise as "mediascan on
# <host>" (lib/batlab-common, senders.json) to the alerts channel, straight to
# MEDIASCAN_DISCORD_WEBHOOK when Apprise is down, and to Pushover when neither
# took it and Pushover is set.
mediascan_notify() {
  local title="$1" body="$2"

  if BATLAB_NOTIFY_FALLBACK="${MEDIASCAN_DISCORD_WEBHOOK:-}" python3 -I \
    "$(dirname -- "${BASH_SOURCE[0]}")/../../lib/batlab-common/batlab_common/notify.py" \
    mediascan alerts warning "$title" --body "$body" >/dev/null 2>&1; then
    return 0
  fi

  if [[ -n "${MEDIASCAN_PUSHOVER_TOKEN:-}" && -n "${MEDIASCAN_PUSHOVER_USER:-}" ]]; then
    curl -sS -m 15 \
      --form-string "token=$MEDIASCAN_PUSHOVER_TOKEN" \
      --form-string "user=$MEDIASCAN_PUSHOVER_USER" \
      --form-string "title=$title" \
      --form-string "message=$body" \
      https://api.pushover.net/1/messages.json >/dev/null 2>&1 || true
  fi
}
