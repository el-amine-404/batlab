#!/usr/bin/env bash
# Bring the data disk back without anyone at the server: the hands-on steps of
# recover-data-disk.sh, as far as software reaches. Run every two minutes
# (batlab-data-disk-revive.timer); does nothing while the disk is mounted and
# answering.
#
# lab2's JMS578 enclosure hangs under USB 3, so every USB 3 port is disabled at
# boot (usb2-only.sh) and the disk is meant to come back at USB 2. It often
# doesn't: it drops off USB 3 and never shows up on the port's USB 2 side,
# which is what a replug fixes (2026-10-01, -03 and -17). In order, until the
# disk is back:
#
#   1. a dead mount (the disk gone or hung under it) is cleared;
#   2. a disk that is present is mounted, with fsck, and the data guard starts
#      the containers it stopped;
#   3. the USB 2 side of the disk's connector is powered off and on, the
#      software replug;
#   4. as a last resort the connector's USB 3 side is enabled again: a disk on
#      USB 3 may hang later, but one off the bus certainly serves nothing. Off
#      with WATCHDOG_REVIVE_USB3_FALLBACK=0.
#
# A disk none of this brings back is announced once, as needing a hand, and
# tried again every WATCHDOG_REVIVE_RETRY_MINUTES.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_watchdog_config
[[ -n "$WATCHDOG_DATA_ROOT" ]] || exit 0

readonly ROOT="$WATCHDOG_DATA_ROOT"
readonly STATE_DIR="${WATCHDOG_STATE_DIR:-/var/lib/batlab-watchdog}"
readonly STATE_FILE="$STATE_DIR/revive"
readonly DISK_FAILS_FILE="$STATE_DIR/data-disk-failures"
readonly DOWN_FILE="$STATE_DIR/data-disk-down"
readonly RETRY_SECONDS=$((${WATCHDOG_REVIVE_RETRY_MINUTES:-10} * 60))
# The USB 2 side of the connector the disk hangs on, as sysfs names it.
readonly USB2_PORT="${WATCHDOG_DATA_USB_PORT:-}"
# Seconds a disk gets to show up after its port comes back.
readonly SETTLE="${WATCHDOG_USB_SETTLE_SECONDS:-45}"
readonly USB2_ATTEMPTS="${WATCHDOG_USB2_ATTEMPTS:-3}"
# What holds the mount open, or watches it, and must let go before it can be
# cleared; started again once the disk is back.
readonly HOLDERS=(smbd nmbd batlab-mediascan-watch)

exec 9>/run/batlab-data-disk-revive.lock
flock -n 9 || exit 0
install -d -m 750 "$STATE_DIR"

spec="$(findmnt --fstab -no SOURCE "$ROOT")"
[[ "$spec" == UUID=* ]] || { echo "fstab must name $ROOT by UUID=, not $spec" >&2; exit 2; }
readonly DEVICE="/dev/disk/by-uuid/${spec#UUID=}"
MOUNT_UNIT="$(systemd-escape -p --suffix=mount "$ROOT")"
readonly MOUNT_UNIT

# attempts since the disk went, when the last one was, whether a hand was asked for
attempts=0 last=0 asked=0
# shellcheck source=/dev/null
[[ -r "$STATE_FILE" ]] && source "$STATE_FILE"
save() { printf 'attempts=%d\nlast=%d\nasked=%d\n' "$attempts" "$last" "$asked" >"$STATE_FILE"; }

# The heartbeat reads the device directly every five minutes and counts the
# runs it could not: reading it here too could leave this script stuck in
# uninterruptible I/O behind a hung disk.
hung() {
  local fails=0
  [[ -r "$DISK_FAILS_FILE" ]] && fails="$(<"$DISK_FAILS_FILE")"
  ((fails >= ${WATCHDOG_DISK_HUNG_AFTER:-2}))
}

mounted() { mountpoint -q "$ROOT"; }

# Mounted from a device that no longer exists, or that the heartbeat finds hung.
dead_mount() {
  mounted || return 1
  [[ -b "$(findmnt -no SOURCE "$ROOT")" ]] || return 0
  hung
}

stopped=()
release_holders() {
  local unit
  for unit in "${HOLDERS[@]}"; do
    if systemctl is-active -q "$unit"; then
      systemctl stop "$unit" || true
      stopped+=("$unit")
    fi
  done
}

restore_holders() {
  ((${#stopped[@]})) && systemctl start "${stopped[@]}" || true
}

clear_dead_mount() {
  echo "Clearing the dead mount at $ROOT"
  release_holders
  # Lazy: nothing can write through a device that is gone, and a hung one
  # would keep a plain umount waiting. The guard's stop path stops the
  # containers as the mount unit goes.
  umount "$ROOT" 2>/dev/null || umount -l "$ROOT"
  systemctl daemon-reload
  echo 0 >"$DISK_FAILS_FILE"
}

wait_for_device() {
  local deadline=$((SECONDS + SETTLE))
  udevadm settle --timeout=10 || true
  while ((SECONDS < deadline)); do
    [[ -b "$DEVICE" ]] && return 0
    sleep 2
  done
  return 1
}

mount_disk() {
  echo "Mounting $ROOT (fsck first)"
  systemctl reset-failed "$MOUNT_UNIT" 2>/dev/null || true
  # Mounting starts batlab-data-guard.service (WantedBy the mount), which starts
  # the containers it stopped.
  systemctl start "$MOUNT_UNIT" && mounted
}

# Speed of the USB device above a block device (480 or 5000).
usb_speed_for() {
  local source="$1" parent dir
  [[ -b "$source" ]] || return 1
  parent="$(lsblk -no PKNAME "$source" 2>/dev/null | head -n 1)"
  [[ -n "$parent" ]] || return 1
  dir="/sys$(udevadm info -q path -n "/dev/$parent")"
  while [[ "$dir" != /sys && ! -r "$dir/speed" ]]; do dir="$(dirname "$dir")"; done
  [[ -r "$dir/speed" ]] && cat "$dir/speed"
}

mounted_usb_speed() {
  local source
  source="$(findmnt -no SOURCE "$ROOT" 2>/dev/null || true)"
  usb_speed_for "$source"
}

# usb1-port1 -> /sys/bus/usb/devices/usb1/1-0:1.0/usb1-port1
port_dir() {
  local hub="${1%-port*}"
  echo "/sys/bus/usb/devices/$hub/${hub#usb}-0:1.0/$1"
}

# Off and on: on a root hub that switches port power this cuts the enclosure's
# VBUS like a replug; on one that doesn't, it still re-runs the port's
# connection, which a bridge stuck between USB 3 and USB 2 needs.
cycle_port() {
  local port="$1"
  [[ -w "$port/disable" ]] || { echo "no such port: $port" >&2; return 1; }
  echo "Power-cycling $(basename "$port")"
  echo 1 >"$port/disable"
  sleep 5
  echo 0 >"$port/disable"
}

usb3_peer() {
  local port="$1" peer
  peer="$(readlink -f "$port/peer" 2>/dev/null)" || return 1
  [[ -w "$peer/disable" ]] && echo "$peer"
}

back() {
  local how="$1" down="" speed=""
  mountpoint -q "$ROOT" || { echo "$ROOT is not mounted after recovery" >&2; return 1; }
  timeout "${WATCHDOG_CHECK_TIMEOUT:-10}" stat "$ROOT" >/dev/null || {
    echo "$ROOT does not answer after recovery" >&2
    return 1
  }
  # The mount starts the guard through systemd. Reconcile once more before a
  # success notification so "back" means all recorded containers are back.
  if [[ -s "$STATE_DIR/stopped-containers" ]]; then
    "$SCRIPT_DIR/data-guard.sh" start || return 1
  fi
  speed="$(mounted_usb_speed 2>/dev/null || true)"
  if [[ "${WATCHDOG_FORCE_USB2:-0}" == 1 && "$speed" != 480 ]]; then
    echo "recovery produced a ${speed:-unknown}M link, not USB 2" >&2
    return 1
  fi
  echo "Data disk back: $how"
  [[ -r "$DOWN_FILE" ]] && down="$(since "$(<"$DOWN_FILE")")"
  watchdog_notify "Data disk revived" success \
    "Disk" "$(data_disk_label)" \
    "Mount" "\`$ROOT\`" \
    "USB link" "${speed:-unknown} Mbit/s" \
    "How" "$how" \
    "Down for" "${down:-unknown}" \
    "Tries" "$attempts"
  attempts=0 last=0 asked=0
  save
}

# ---- healthy: finish recovery, and enforce USB 2 on lab2 -----------------------------

migrating_usb2=0
if mounted && ! dead_mount; then
  # A prior partial `docker start` is retried transactionally every two minutes.
  if [[ -s "$STATE_DIR/stopped-containers" ]]; then
    "$SCRIPT_DIR/data-guard.sh" start || exit 1
  fi
  speed="$(mounted_usb_speed 2>/dev/null || true)"
  if [[ "${WATCHDOG_FORCE_USB2:-0}" != 1 || -z "$speed" || "$speed" -le 480 ]]; then
    if ((attempts)); then attempts=0 last=0 asked=0; save; fi
    exit 0
  fi

  echo "Data disk is at ${speed}M; migrating it safely to USB 2"
  release_holders
  trap restore_holders EXIT
  # This invokes the guard, which records and stops every disk-backed
  # container before the USB link is changed.
  systemctl stop "$MOUNT_UNIT"
  mounted && { echo "$ROOT did not unmount" >&2; exit 1; }
  migrating_usb2=1
fi

# ---- not yet time for another try -----------------------------------------------------

now="$(date +%s)"
if ((!migrating_usb2 && asked && now - last < RETRY_SECONDS)); then
  exit 0
fi
attempts=$((attempts + 1)) last=$now
save

trap restore_holders EXIT

dead_mount && clear_dead_mount

# 2. Present already (back by itself, or a replug): mount it.
device_speed="$(usb_speed_for "$DEVICE" 2>/dev/null || true)"
if ((!migrating_usb2)) && [[ -b "$DEVICE" ]] &&
  [[ "${WATCHDOG_FORCE_USB2:-0}" != 1 || -z "$device_speed" || "$device_speed" -le 480 ]]; then
  mount_disk && { back "mounted (the disk was on the bus)"; exit 0; }
fi

if [[ -z "$USB2_PORT" ]]; then
  echo "WATCHDOG_DATA_USB_PORT is not set; cannot power-cycle the disk's port" >&2
else
  port="$(port_dir "$USB2_PORT")"

  # 3. Keep USB 3 disabled and retry the software replug on USB 2. Some
  # JMS578 bridges need more than one cycle after moving between buses.
  if peer="$(usb3_peer "$port")"; then echo 1 >"$peer/disable"; fi
  for ((try = 1; try <= USB2_ATTEMPTS; try++)); do
    echo "USB 2 recovery attempt $try/$USB2_ATTEMPTS"
    if cycle_port "$port" && wait_for_device && mount_disk; then
      back "USB 2 restored after $try attempt(s)"
      exit 0
    fi
  done

  # 4. Last resort: let it back on at USB 3.
  if [[ "${WATCHDOG_REVIVE_USB3_FALLBACK:-0}" == 1 ]] && peer="$(usb3_peer "$port")"; then
    echo "Enabling the USB 3 side, $(basename "$peer")"
    echo 0 >"$peer/disable"
    if wait_for_device && mount_disk; then
      back "on USB 3, its USB 2 side gave nothing: the JMS578 bridge may hang under load, replug it at a quiet moment"
      exit 0
    fi
    # Back to USB 2 only, as booted.
    echo 1 >"$peer/disable" || true
  fi
fi

if ((!asked)); then
  local problem="off the USB bus: the system doesn't see it"
  mounted && problem="mounted, but not answering reads"
  watchdog_notify "Data disk needs a replug" failure \
    "Disk" "$(data_disk_label)" \
    "Mount" "\`$ROOT\`" \
    "Problem" "$problem" \
    "Log" "$(code_block "$(data_disk_log 60)")" \
    "Tried" "clearing the mount, mounting, power-cycling its USB 2 port ${USB2_ATTEMPTS} time(s)$([[ "${WATCHDOG_REVIVE_USB3_FALLBACK:-0}" == 1 ]] && echo ", USB 3")" \
    "Do" "unplug the enclosure's USB and power, wait 10 s, plug both back" \
    "Next try" "in ${WATCHDOG_REVIVE_RETRY_MINUTES:-10} min, then every ${WATCHDOG_REVIVE_RETRY_MINUTES:-10} min"
  asked=1
  save
fi
echo "The data disk is still gone (attempt $attempts)."
exit 1
