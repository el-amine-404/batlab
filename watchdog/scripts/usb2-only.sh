#!/usr/bin/env bash
# Disable the USB 3 half of every root-hub port, so a device plugged in falls
# back to the port's USB 2 peer at 480M. For a disk enclosure whose bridge
# resets under USB 3 on a host without USB 2 ports (hosts/lab2/README.md).
# A device already connected at USB 3 drops off and comes back at USB 2.
#
# WATCHDOG_USB3_PORTS in the watchdog config names USB 3 ports to leave alone,
# for a device that is fine at USB 3 (lab2: usb2-port2, the SSD).

set -Eeuo pipefail
shopt -s nullglob

config="${WATCHDOG_CONFIG_FILE:-/etc/batlab-watchdog/watchdog.env}"
# shellcheck source=/dev/null
[[ -r "$config" ]] && source "$config"
read -ra keep <<<"${WATCHDOG_USB3_PORTS:-}"

disabled=0 dropped=()
for hub in /sys/bus/usb/devices/usb*; do
  (($(<"$hub/speed") >= 5000)) || continue
  for port in "$hub"/*-0:1.0/usb*-port*; do
    if [[ " ${keep[*]} " == *" $(basename "$port") "* ]]; then
      echo 0 >"$port/disable"
      echo "Kept at USB 3: $(basename "$port")"
      continue
    fi
    [[ "$(<"$port/disable")" == 1 ]] && continue
    # Something on it now: it drops off, and must come back on the USB 2 side.
    [[ -e "$port/device" ]] && dropped+=("$port")
    echo 1 >"$port/disable"
    disabled=$((disabled + 1))
  done
done
echo "USB 3 ports disabled: $disabled"

# The JMS578 does not come back by itself: dropped off USB 3, it never showed
# up on the port's USB 2 side after the boots of 2026-10-01, -03 and -17 until
# it was replugged. Power-cycling the USB 2 side is that replug.
for port in "${dropped[@]}"; do
  peer="$(readlink -f "$port/peer" 2>/dev/null)" || continue
  [[ -w "$peer/disable" ]] || continue
  sleep 2
  echo 1 >"$peer/disable"
  sleep 5
  echo 0 >"$peer/disable"
  echo "Power-cycled $(basename "$peer"), the USB 2 side of $(basename "$port")"
done
