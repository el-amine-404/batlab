#!/usr/bin/env bash
# One journal line about a Wi-Fi link, for a history of signal and dropouts:
#   journalctl -u batlab-wifi-log --since today
# wpa_supplicant logs each disconnect itself (CTRL-EVENT-DISCONNECTED).

set -Eeuo pipefail

iface="${1:?usage: $0 <wifi interface>}"

iw dev "$iface" link | awk -v iface="$iface" '
  /^Not connected/ { print iface " not connected"; done = 1; exit }
  $1 == "SSID:"    { ssid = $2 }
  $1 == "freq:"    { freq = $2 }
  $1 == "signal:"  { signal = $2 }
  /rx bitrate:/    { rx = $3 }
  /tx bitrate:/    { tx = $3 }
  END {
    if (!done) printf "%s ssid=%s freq=%sMHz signal=%sdBm rx=%sMbit/s tx=%sMbit/s\n", iface, ssid, freq, signal, rx, tx
  }'
