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

systemctl is-active --quiet smartmontools.service
systemctl is-active --quiet batlab-watchdog-heartbeat.timer
systemctl is-active --quiet batlab-oom-watch.timer
ufw status verbose
echo "lab2 production baseline installed"
