#!/usr/bin/env bash
# Disable the USB 3 half of every root-hub port, so a device plugged in falls
# back to the port's USB 2 peer at 480M. For a disk enclosure whose bridge
# resets under USB 3 on a host without USB 2 ports (hosts/lab2/README.md).
# A device already connected at USB 3 drops off and comes back at USB 2.

set -Eeuo pipefail
shopt -s nullglob

disabled=0
for hub in /sys/bus/usb/devices/usb*; do
  (($(<"$hub/speed") >= 5000)) || continue
  for port in "$hub"/*-0:1.0/usb*-port*; do
    [[ "$(<"$port/disable")" == 1 ]] && continue
    echo 1 >"$port/disable"
    disabled=$((disabled + 1))
  done
done

echo "USB 3 ports disabled: $disabled"
