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
readonly STATE_DIR="/var/lib/batlab-watchdog"
readonly STATE_FILE="$STATE_DIR/revive"
readonly DISK_FAILS_FILE="$STATE_DIR/data-disk-failures"
readonly DOWN_FILE="$STATE_DIR/data-disk-down"
readonly RETRY_SECONDS=$((${WATCHDOG_REVIVE_RETRY_MINUTES:-10} * 60))
# The USB 2 side of the connector the disk hangs on, as sysfs names it.
readonly USB2_PORT="${WATCHDOG_DATA_USB_PORT:-}"
# Seconds a disk gets to show up after its port comes back.
readonly SETTLE=45
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
  local how="$1" down=""
  echo "Data disk back: $how"
  [[ -r "$DOWN_FILE" ]] && down="$(since "$(<"$DOWN_FILE")")"
  watchdog_notify "Data disk revived" success \
    "Disk" "$(data_disk_label)" \
    "Mount" "\`$ROOT\`" \
    "How" "$how" \
    "Down for" "${down:-unknown}" \
    "Tries" "$attempts"
  attempts=0 last=0 asked=0
  save
}

# ---- healthy: nothing to do ----------------------------------------------------------

if mounted && ! dead_mount; then
  if ((attempts)); then attempts=0 last=0 asked=0; save; fi
  exit 0
fi

# ---- not yet time for another try -----------------------------------------------------

now="$(date +%s)"
if ((asked && now - last < RETRY_SECONDS)); then
  exit 0
fi
attempts=$((attempts + 1)) last=$now
save

trap restore_holders EXIT

dead_mount && clear_dead_mount

# 2. Present already (back by itself, or a replug): mount it.
if [[ -b "$DEVICE" ]]; then
  mount_disk && { back "mounted (the disk was on the bus)"; exit 0; }
fi

if [[ -z "$USB2_PORT" ]]; then
  echo "WATCHDOG_DATA_USB_PORT is not set; cannot power-cycle the disk's port" >&2
else
  port="$(port_dir "$USB2_PORT")"

  # 3. The software replug, on the USB 2 side.
  if cycle_port "$port" && wait_for_device && mount_disk; then
    back "its USB 2 port was power-cycled"
    exit 0
  fi

  # 4. Last resort: let it back on at USB 3.
  if [[ "${WATCHDOG_REVIVE_USB3_FALLBACK:-1}" == 1 ]] && peer="$(usb3_peer "$port")"; then
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
    "Tried" "clearing the mount, mounting, power-cycling its USB port$([[ "${WATCHDOG_REVIVE_USB3_FALLBACK:-1}" == 1 ]] && echo ", USB 3")" \
    "Do" "unplug the enclosure's USB and power, wait 10 s, plug both back" \
    "Next try" "in ${WATCHDOG_REVIVE_RETRY_MINUTES:-10} min, then every ${WATCHDOG_REVIVE_RETRY_MINUTES:-10} min"
  asked=1
  save
fi
echo "The data disk is still gone (attempt $attempts)."
exit 1
