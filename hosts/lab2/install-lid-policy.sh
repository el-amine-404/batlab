#!/usr/bin/env bash
# Install or remove lab2's server-safe lid policy.

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly UNIT=batlab-lid-screen.service
readonly LOGIND_DROPIN=60-batlab-server-lid.conf

[[ "$(id -u)" == 0 ]] || { echo "run with sudo" >&2; exit 1; }

reload_logind() {
  systemctl kill --kill-whom=main --signal=HUP systemd-logind.service
}

uninstall() {
  systemctl disable --now "$UNIT" 2>/dev/null || true
  rm -f "/etc/systemd/system/$UNIT" \
    "/etc/systemd/logind.conf.d/$LOGIND_DROPIN" \
    /usr/local/libexec/batlab-lid-screen
  systemctl daemon-reload
  reload_logind
  echo "Removed lab2 lid policy; systemd-logind defaults apply again."
}

if [[ "${1:-}" == --uninstall ]]; then
  uninstall
  exit
elif (($#)); then
  echo "usage: sudo $0 [--uninstall]" >&2
  exit 2
fi

install -d -m 0755 /etc/systemd/logind.conf.d /etc/systemd/system /usr/local/libexec
install -m 0644 "$SCRIPT_DIR/logind.conf.d/$LOGIND_DROPIN" \
  "/etc/systemd/logind.conf.d/$LOGIND_DROPIN"
install -m 0755 "$SCRIPT_DIR/scripts/lid_screen.py" /usr/local/libexec/batlab-lid-screen
install -m 0644 "$SCRIPT_DIR/systemd/$UNIT" "/etc/systemd/system/$UNIT"

# Remove the names used by the initial one-off installation.
systemctl disable --now lab2-lid-screen.service 2>/dev/null || true
rm -f /etc/systemd/system/lab2-lid-screen.service \
  /etc/systemd/logind.conf.d/60-lab2-server-lid.conf \
  /usr/local/sbin/lab2-lid-screen

systemctl daemon-reload
systemctl enable --now "$UNIT"
reload_logind

for property in HandleLidSwitch HandleLidSwitchExternalPower HandleLidSwitchDocked; do
  value="$(busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
    org.freedesktop.login1.Manager "$property")"
  [[ "$value" == 's "ignore"' ]] || {
    echo "$property is $value, expected ignore" >&2
    exit 1
  }
  printf '%s=%s\n' "$property" "$value"
done

systemctl --quiet is-enabled "$UNIT"
systemctl --quiet is-active "$UNIT"
echo "$UNIT is enabled and active"
