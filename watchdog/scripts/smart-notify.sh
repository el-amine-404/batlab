#!/usr/bin/env bash
# Posts a smartd warning to Discord. Installed as /etc/smartmontools/run.d/20discord,
# which smartd-runner calls with the SMARTD_* variables set and the warning on stdin.

set -Eeuo pipefail

readonly COMMON="${BATLAB_WATCHDOG_COMMON:-/home/potato/batlab/watchdog/scripts/common.sh}"
readonly RED=15158332
readonly ORANGE=15105570
readonly GREY=9807270

# A missing watchdog install must not turn every disk warning into a run-parts
# error; smartd still logs the warning either way.
if [[ ! -r "$COMMON" ]]; then
  echo "smart-notify: cannot read $COMMON" >&2
  exit 0
fi

# shellcheck source=/dev/null
source "$COMMON"
load_watchdog_config

device="${SMARTD_DEVICESTRING:-${SMARTD_DEVICE:-unknown device}}"
failure="${SMARTD_FAILTYPE:-unknown}"
message="${SMARTD_MESSAGE:-no message}"
detail="${SMARTD_FULLMESSAGE:-}"
if [[ -z "$detail" && ! -t 0 ]]; then
  detail="$(timeout 5 cat 2>/dev/null || true)"
fi

case "$failure" in
  EmailTest)
    title="⚪ SMART test message"
    color="$GREY"
    ;;
  Temperature | Usage | SelfTest | ErrorCount | CurrentPendingSector | OfflineUncorrectableSector)
    title="🟠 SMART warning: $failure"
    color="$ORANGE"
    ;;
  *)
    title="🔴 Disk problem: $failure"
    color="$RED"
    ;;
esac

fields=(Device "$device" Problem "$message")
[[ -n "${SMARTD_TFIRST:-}" ]] && fields+=("First seen" "$SMARTD_TFIRST")
[[ -n "$detail" ]] && fields+=(Detail "$(printf '%s' "$detail" | tail -c 900)")

watchdog_notify "$title" "$color" "${fields[@]}"
