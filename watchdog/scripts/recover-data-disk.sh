#!/usr/bin/env bash
# Bring back a data disk that hangs or dropped off the bus: pause what reads it,
# unmount, have it power-cycled by hand, check SMART, mount it again. The data
# guard stops and starts the data-disk containers with the mount.
#
#   sudo watchdog/scripts/recover-data-disk.sh [--usb2]
#
# --usb2 puts every USB port at USB 2 speed before the disk is plugged back in,
# and once the disk is confirmed at 480M installs batlab-usb2-only.service from
# hosts/$HOST_PROFILE/systemd/ so it stays that way after a reboot.

set -Euo pipefail

readonly MOUNT=/mnt/storage/data
readonly MOUNT_UNIT=mnt-storage-data.mount
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly REPO="${SCRIPT_DIR%/watchdog/scripts}"

usb2=false
[[ "${1:-}" == --usb2 ]] && usb2=true

[[ "$(id -u)" == 0 ]] || { echo "run with sudo" >&2; exit 1; }

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ask() { local _; read -r -p "$* " _ </dev/tty; }

uuid="$(awk -v m="$MOUNT" '$2 == m && $1 ~ /^UUID=/ { sub(/^UUID=/, "", $1); print $1 }' /etc/fstab)"
[[ -n "$uuid" ]] || { echo "no UUID= line for $MOUNT in /etc/fstab" >&2; exit 1; }
readonly PART="/dev/disk/by-uuid/$uuid"

# Speed of the USB device a block device hangs off, e.g. 480 or 5000.
usb_speed() {
  local dir
  dir="/sys$(udevadm info -q path -n "$1")"
  while [[ "$dir" != /sys && ! -r "$dir/speed" ]]; do dir="$(dirname "$dir")"; done
  [[ -r "$dir/speed" ]] && cat "$dir/speed"
}

step "Pausing everything that reads the disk"
paused=()
for unit in smbd.service nmbd.service batlab-mediascan-watch.service \
  $(systemctl list-units --plain --no-legend --state=active 'batlab-*.timer' | awk '{ print $1 }'); do
  systemctl is-active -q "$unit" || continue
  paused+=("$unit")
  timeout 60 systemctl stop "$unit" || echo "  $unit did not stop in time; the replug will free it"
done
echo "  paused: ${paused[*]:-none}"

step "Unmounting $MOUNT (the data guard stops the data-disk containers)"
if timeout 180 systemctl stop "$MOUNT_UNIT"; then
  echo "  unmounted"
else
  echo "  still busy: processes are stuck on the dead disk; unplugging it frees them"
fi

if $usb2; then
  step "Switching USB ports to USB 2"
  "$SCRIPT_DIR/usb2-only.sh"
fi

step "Power-cycle the disk"
echo "  Unplug the enclosure's USB cable AND its power cable."
ask "  Wait 30 seconds, then press Enter."

for _ in $(seq 60); do mountpoint -q "$MOUNT" || break; sleep 2; done
if mountpoint -q "$MOUNT"; then
  echo "  the dead mount is still there; detaching it (nothing can write to a device that is gone)"
  umount -l "$MOUNT"
fi
systemctl reset-failed "$MOUNT_UNIT" 2>/dev/null || true
systemctl daemon-reload

echo "  Plug the power cable in first, then the USB cable."
ask "  Press Enter once both are in."

step "Waiting for the disk"
for _ in $(seq 60); do [[ -e "$PART" ]] && break; sleep 1; done
if [[ ! -e "$PART" ]]; then
  echo "  the disk did not appear after 60 s; check the cables and 'lsusb', then run this again" >&2
  exit 1
fi
disk="/dev/$(lsblk -no PKNAME "$PART")"
speed="$(usb_speed "$disk")"
echo "  $disk is back, USB link at ${speed:-unknown}M"

step "SMART health of $disk"
smart="$(smartctl -d sat -H -A "$disk")"
smart_rc=$?
grep -E 'overall-health|Reallocated_Sector|Current_Pending|Offline_Uncorrectable|UDMA_CRC|Power_On_Hours' <<<"$smart" | sed 's/^/  /'
raw() { awk -v id="$1" '$1 == id { print $10 }' <<<"$smart"; }
bad=false
((smart_rc & 8)) && bad=true # the disk says it is failing
for id in 5 197 198; do [[ "$(raw "$id")" =~ ^[0-9]+$ ]] && (($(raw "$id") > 0)) && bad=true; done
if $bad; then
  echo
  echo "  WARNING: the drive itself reports damage (reallocated, pending or uncorrectable sectors)."
  echo "  Copy what matters off it before anything else; do not run long jobs on it."
  read -r -p "  Mount it anyway? [y/N] " answer </dev/tty
  [[ "$answer" == [yY]* ]] || { echo "  left unmounted"; exit 1; }
else
  echo "  no reallocated, pending or uncorrectable sectors"
fi

step "Mounting $MOUNT (fsck runs first)"
if ! systemctl start "$MOUNT_UNIT"; then
  echo "  mount failed; see: journalctl -b -u systemd-fsck@* -u $MOUNT_UNIT" >&2
  exit 1
fi
echo "  mounted"

step "Read test: 1 GB straight from the disk"
if ! timeout 120 dd if="$disk" of=/dev/null bs=1M count=1024 skip=$((RANDOM * 64)) iflag=direct status=none; then
  echo "  the read failed or took over 2 minutes: the disk is not healthy yet" >&2
  journalctl -k -n 10 --no-pager | sed 's/^/  /'
  exit 1
fi
echo "  ok"

step "Resuming paused units"
((${#paused[@]})) && systemctl start "${paused[@]}"
echo "  started: ${paused[*]:-none}"

if $usb2; then
  step "Making USB 2 permanent"
  if [[ "$speed" == 480 ]]; then
    profile="$(awk -F= '$1 == "HOST_PROFILE" { gsub(/["\047]/, "", $2); print $2 }' "$REPO/compose/.env")"
    install -m 755 "$SCRIPT_DIR/usb2-only.sh" /usr/local/sbin/batlab-usb2-only
    install -m 644 "$REPO/hosts/$profile/systemd/batlab-usb2-only.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable batlab-usb2-only.service
  else
    echo "  the disk came back at ${speed:-unknown}M, not 480M; not installing the boot unit" >&2
  fi
fi

step "Done"
docker ps -a --filter status=exited --filter status=created --format '  not running: {{.Names}} ({{.Status}})'
echo "  Healthchecks: the heartbeat may have paged while the disk was out; it clears on the next run."
