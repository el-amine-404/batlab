#!/usr/bin/env bash
# Idempotently install lab2's host security, monitoring and self-healing policy.

set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run with sudo: sudo hosts/lab2/install-production-baseline.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly REPO="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
readonly LAN_CIDR="${LAB2_LAN_CIDR:-192.168.1.0/24}"

for command_name in apt-get install systemctl; do
  command -v "$command_name" >/dev/null || { echo "Missing $command_name" >&2; exit 1; }
done

export DEBIAN_FRONTEND=noninteractive
apt-get install -y smartmontools ufw

install -m 644 "$SCRIPT_DIR/smartd.conf" /etc/smartd.conf
install -m 755 "$REPO/watchdog/scripts/smart-notify.sh" /etc/smartmontools/run.d/20discord
install -d -m 755 /etc/systemd/system/smartmontools.service.d
install -m 644 "$SCRIPT_DIR/systemd/smartmontools.service.d/restart.conf" \
  /etc/systemd/system/smartmontools.service.d/restart.conf

install -d -m 755 /etc/systemd/system/batlab-restic-backup.service.d
install -m 644 "$SCRIPT_DIR/systemd/batlab-restic-backup.service.d/retry.conf" \
  /etc/systemd/system/batlab-restic-backup.service.d/retry.conf
install -m 644 "$REPO/watchdog/systemd/batlab-watchdog-heartbeat.service" \
  "$REPO/watchdog/systemd/batlab-watchdog-heartbeat.timer" \
  "$REPO/watchdog/systemd/batlab-oom-watch.service" \
  "$REPO/watchdog/systemd/batlab-oom-watch.timer" /etc/systemd/system/
install -m 644 "$REPO/mediascan/systemd/batlab-mediascan-sweep.service" \
  "$REPO/mediascan/systemd/batlab-mediascan-deep.service" /etc/systemd/system/
install -m 644 "$REPO/restic/systemd/batlab-restic-restore-test.service" \
  "$REPO/restic/systemd/batlab-restic-restore-test.timer" /etc/systemd/system/

# The JMS578 enclosure is reliable only at USB 2 on lab2. Install the boot
# policy and the runtime recovery path together so a fresh host cannot get one
# half without the other. Runtime migration is performed by the revival
# service after it has cleanly stopped the mount and its containers.
install -m 755 "$REPO/watchdog/scripts/usb2-only.sh" /usr/local/sbin/batlab-usb2-only
install -m 644 "$SCRIPT_DIR/systemd/batlab-usb2-only.service" \
  "$REPO/watchdog/systemd/batlab-data-disk-revive.service" \
  "$REPO/watchdog/systemd/batlab-data-disk-revive.timer" \
  "$REPO/watchdog/systemd/batlab-data-guard.service" \
  "$REPO/watchdog/systemd/batlab-data-guard-boot.service" /etc/systemd/system/

watchdog_config=/etc/batlab-watchdog/watchdog.env
if [[ ! -e "$watchdog_config" ]]; then
  install -d -m 750 /etc/batlab-watchdog
  install -m 600 "$REPO/watchdog/conf/watchdog.env.example" "$watchdog_config"
fi
set_watchdog_value() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$watchdog_config"; then
    sed -i -E "s|^${key}=.*|${key}=${value}|" "$watchdog_config"
  else
    printf '%s=%s\n' "$key" "$value" >>"$watchdog_config"
  fi
}
set_watchdog_value WATCHDOG_DATA_ROOT /mnt/storage/data
set_watchdog_value WATCHDOG_DATA_USB_PORT usb1-port1
set_watchdog_value WATCHDOG_FORCE_USB2 1
set_watchdog_value WATCHDOG_USB3_PORTS usb2-port2
set_watchdog_value WATCHDOG_USB2_ATTEMPTS 3
set_watchdog_value WATCHDOG_REVIVE_USB3_FALLBACK 0

# Install allow rules before enabling the firewall so an SSH deployment cannot
# lock itself out. DNS and DHCP must accept clients before they have an address;
# administration and Samba remain limited to the local subnet.
sed -i 's/^DEFAULT_FORWARD_POLICY=.*/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow from "$LAN_CIDR" to any port 22 proto tcp comment 'batlab ssh'
ufw allow from "$LAN_CIDR" to any port 3000 proto tcp comment 'batlab AdGuard UI'
ufw allow from "$LAN_CIDR" to any port 445 proto tcp comment 'batlab Samba'
ufw allow from "$LAN_CIDR" to any port 137:138 proto udp comment 'batlab Samba discovery'
ufw allow in from 172.19.0.0/16 comment 'batlab containers to host'
# Remove the older narrower DNS rule before adding the authoritative one. DHCP
# clients need DNS before they necessarily have an address in LAN_CIDR.
ufw --force delete allow from "$LAN_CIDR" to any port 53 || true
ufw allow 53 comment 'batlab DNS'
ufw allow 67/udp comment 'batlab DHCP'
ufw --force enable

systemctl daemon-reload
# Clear historical states after installing policies that distinguish notified
# media findings (successful exit 1) from real scanner failures (exit 2).
systemctl reset-failed smartmontools.service batlab-restic-backup.service \
  batlab-mediascan-sweep.service batlab-mediascan-deep.service || true
systemctl enable --now smartmontools.service batlab-watchdog-heartbeat.timer batlab-oom-watch.timer
systemctl enable --now batlab-restic-restore-test.timer
systemctl enable batlab-usb2-only.service batlab-data-guard.service batlab-data-guard-boot.service
systemctl enable --now batlab-data-disk-revive.timer
mountpoint -q /mnt/storage/data && systemctl start batlab-data-guard.service

systemctl is-active --quiet smartmontools.service
systemctl is-active --quiet batlab-watchdog-heartbeat.timer
systemctl is-active --quiet batlab-oom-watch.timer
systemctl is-active --quiet batlab-restic-restore-test.timer
systemctl is-active --quiet batlab-data-disk-revive.timer
systemctl is-enabled --quiet batlab-usb2-only.service
ufw status verbose
echo "lab2 production baseline installed"
