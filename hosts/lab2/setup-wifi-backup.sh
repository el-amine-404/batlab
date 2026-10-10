#!/usr/bin/env bash
# Join lab2's Wi-Fi card to the house Wi-Fi at 192.168.1.202 as a way out when
# the cable is down, have the heartbeat fail without the cable, and log the
# Wi-Fi link every minute. Run on lab2:
#   sudo hosts/lab2/setup-wifi-backup.sh
# The password is read from the terminal; only its hash is stored.

set -Eeuo pipefail

readonly IFACE=wlp2s0
readonly WIRED=enp0s31f6
readonly WPA_CONF=/etc/wpa_supplicant/$IFACE.conf
readonly WATCHDOG_ENV=/etc/batlab-watchdog/watchdog.env
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly HERE

[[ "$(id -u)" == 0 ]] || { echo "run with sudo" >&2; exit 1; }
# A rerun finds .202 on this host already, which answers its own ping.
if ! ip -4 -o addr show | grep -q ' 192\.168\.1\.202/' &&
  ping -c 2 -W 1 192.168.1.202 >/dev/null 2>&1; then
  echo "192.168.1.202 already answers; pick another address" >&2
  exit 1
fi

# a_2.4, not a_5: a_5 is WPA3-only, and WPA3 (SAE) cannot use a hashed
# password, so it would sit on lab2 in plain text. A backup link needs range
# more than speed, and the TV is on a_2.4 too, so the Wi-Fi log watches its band.
read -r -p "Wi-Fi name [a_2.4]: " ssid </dev/tty
ssid="${ssid:-a_2.4}"
read -r -s -p "Wi-Fi password for $ssid: " pass </dev/tty
echo

umask 077
{
  echo "ctrl_interface=/run/wpa_supplicant"
  # Strip the plain-text password line wpa_passphrase adds as a comment.
  wpa_passphrase "$ssid" <<<"$pass" | grep -v '^[[:space:]]*#psk='
} >"$WPA_CONF"
unset pass
echo "wrote $WPA_CONF (root only, password hashed)"

install -m 644 "$HERE/sysctl.d/90-wifi-backup.conf" /etc/sysctl.d/
sysctl -q -p /etc/sysctl.d/90-wifi-backup.conf
echo "applied routing and ARP settings"

install -m 644 "$HERE/network/$IFACE" "/etc/network/interfaces.d/$IFACE"
ifdown "$IFACE" 2>/dev/null || true
ifup "$IFACE"

echo "waiting for the Wi-Fi to connect..."
for _ in $(seq 30); do iw dev "$IFACE" link | grep -q '^Connected' && break; sleep 1; done
if ! iw dev "$IFACE" link | grep -q '^Connected'; then
  echo "not connected after 30 s; check the name and password, then run this again" >&2
  echo "(a WPA3-only network never connects with this setup: it writes a WPA2 key)" >&2
  journalctl -n 15 --no-pager -t wpa_supplicant >&2 || true
  exit 1
fi

if grep -q '^WATCHDOG_WIRED_IFACE=' "$WATCHDOG_ENV"; then
  sed -i "s/^WATCHDOG_WIRED_IFACE=.*/WATCHDOG_WIRED_IFACE=$WIRED/" "$WATCHDOG_ENV"
else
  printf '\n# The heartbeat fails while the cable has no link (Wi-Fi backup).\nWATCHDOG_WIRED_IFACE=%s\n' "$WIRED" >>"$WATCHDOG_ENV"
fi
echo "heartbeat now checks the cable on $WIRED"

install -m 644 "$HERE/systemd/batlab-wifi-log.service" "$HERE/systemd/batlab-wifi-log.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now batlab-wifi-log.timer

echo
echo "== Result"
iw dev "$IFACE" link | sed -n '1,3p;/signal/p;/tx bitrate/p'
ip -br addr show "$IFACE"
ip route | grep -E "default|192.168.1.0"
if ping -c 2 -W 2 -I "$IFACE" 1.1.1.1 >/dev/null; then
  echo "internet over Wi-Fi: ok"
else
  echo "internet over Wi-Fi: FAILED" >&2
fi
