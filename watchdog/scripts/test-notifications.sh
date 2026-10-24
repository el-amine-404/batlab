#!/usr/bin/env bash
# Send harmless cards through the two host-level emergency notification paths.

set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run with sudo: sudo watchdog/scripts/test-notifications.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# systemd normally supplies these variables to oom_watch.py; reproduce that
# environment so this manual test exercises the same Apprise/Discord fallback.
set -a
# shellcheck source=/dev/null
source "${WATCHDOG_CONFIG_FILE:-/etc/batlab-watchdog/watchdog.env}"
set +a

"$SCRIPT_DIR/oom_watch.py" --test-notification
env SMARTD_FAILTYPE=EmailTest \
  SMARTD_DEVICESTRING=/dev/disk/by-id/test-device \
  SMARTD_MESSAGE='SMART notification path is active' \
  /etc/smartmontools/run.d/20discord </dev/null

echo "Sent OOM and SMART test notifications"
