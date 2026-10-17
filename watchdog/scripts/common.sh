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

# ---- what a card says about the data disk ---------------------------------------------
# A card names the disk it is about: its model, size, device and UUID, and
# where it mounts. A disk off the bus can't be asked, so the last answer it gave
# (data_disk_seen, run while it is mounted) is kept and used.
readonly WATCHDOG_DISK_LABEL_FILE="/var/lib/batlab-watchdog/data-disk-label"

data_disk_uuid() {
  local spec
  spec="$(findmnt --fstab -no SOURCE "$WATCHDOG_DATA_ROOT" 2>/dev/null || true)"
  echo "${spec#UUID=}"
}

# Remembers the disk's model and size while it answers.
data_disk_seen() {
  local uuid device parent label
  uuid="$(data_disk_uuid)"
  device="$(readlink -f "/dev/disk/by-uuid/$uuid" 2>/dev/null || true)"
  [[ -b "$device" ]] || return 0
  parent="$(lsblk -no PKNAME "$device" 2>/dev/null | head -n 1)"
  label="$(lsblk -dno VENDOR,MODEL,SIZE "/dev/${parent:-${device#/dev/}}" 2>/dev/null | xargs)"
  [[ -n "$label" ]] || return 0
  install -d -m 750 "$(dirname "$WATCHDOG_DISK_LABEL_FILE")"
  echo "$label" >"$WATCHDOG_DISK_LABEL_FILE"
}

# "JMicron WDC WD40EFPX 3.6T, sdb1, UUID b0a17133": now, or as last seen.
data_disk_label() {
  local uuid device where
  uuid="$(data_disk_uuid)"
  data_disk_seen
  device="$(readlink -f "/dev/disk/by-uuid/$uuid" 2>/dev/null || true)"
  if [[ -b "$device" ]]; then
    where="${device#/dev/}"
  else
    where="not on the bus"
  fi
  echo "$(cat "$WATCHDOG_DISK_LABEL_FILE" 2>/dev/null || echo "data disk"), $where, UUID ${uuid:0:8}"
}

# The kernel's last lines about USB disks: what a reader needs to see why.
data_disk_log() {
  journalctl -k --since "-${1:-30} min" --no-pager -o short-iso 2>/dev/null |
    grep -E "usb [0-9]-[0-9.]+:|sd[a-z]+[0-9]*[:\] ]|I/O error|Hardware Error|EXT4-fs (error|warning)" |
    tail -n "${2:-4}" | cut -c1-160 | sed -E 's/^[0-9T:-]+([0-9]{2}:[0-9]{2}:[0-9]{2})[^ ]* [^ ]+ kernel: /\1 /'
}

# A Discord code block, or nothing when there is nothing to show.
code_block() {
  [[ -n "$1" ]] && printf '```\n%s\n```' "$1"
}

# "2 h 14 min" since a Unix time.
since() {
  local seconds=$(($(date +%s) - $1))
  if ((seconds < 60)); then echo "${seconds} s"
  elif ((seconds < 3600)); then echo "$((seconds / 60)) min"
  else echo "$((seconds / 3600)) h $((seconds % 3600 / 60)) min"
  fi
}

