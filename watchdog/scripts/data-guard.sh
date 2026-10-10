#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_watchdog_config
: "${WATCHDOG_DATA_ROOT:?the data guard needs WATCHDOG_DATA_ROOT}"

readonly STATE_DIR="/var/lib/batlab-watchdog"
readonly STOPPED_FILE="$STATE_DIR/stopped-containers"
readonly RED=15158332
readonly GREEN=3066993

# name, restart policy, running, whether the last start failed; only for
# containers bind-mounting something under the data root.
data_containers() {
  docker ps -aq |
    xargs -r docker inspect -f '{{.Name}} {{or .HostConfig.RestartPolicy.Name "no"}} {{.State.Running}} {{if .State.Error}}failed{{else}}ok{{end}}{{range .Mounts}} {{.Source}}{{end}}' |
    awk -v d="$WATCHDOG_DATA_ROOT" '{ for (i = 5; i <= NF; i++) if ($i == d || index($i, d "/") == 1) { print substr($1, 2), $2, $3, $4; next } }'
}

stop_data_containers() {
  local reason="$1" names
  names="$(data_containers | awk '$3 == "true" { print $1 }' | xargs)"

  echo "$reason; stopping: ${names:-none}"
  # Before stopping: a container stuck on a hung disk can keep docker stop
  # waiting, and the card must go out regardless.
  watchdog_notify "🔴 Data disk unavailable" "$RED" \
    "Mount" "\`$WATCHDOG_DATA_ROOT\`" \
    "Reason" "$reason" \
    "Stopping" "${names// /, }"

  if [[ -n "$names" ]]; then
    install -d -m 750 "$STATE_DIR"
    tr ' ' '\n' <<<"$names" >>"$STOPPED_FILE"
    # shellcheck disable=SC2086
    docker stop $names >/dev/null
  fi
}

start_data_containers() {
  local wanted="" names

  if ! mountpoint -q "$WATCHDOG_DATA_ROOT"; then
    echo "$WATCHDOG_DATA_ROOT is not mounted" >&2
    exit 1
  fi

  [[ -r "$STOPPED_FILE" ]] && wanted="$(xargs <"$STOPPED_FILE")"

  # Only containers this guard stopped, or that Docker failed to start while the
  # disk was missing. Containers stopped on purpose stay stopped.
  names="$(data_containers | awk -v wanted="$wanted" '
    BEGIN { n = split(wanted, w, " "); for (i = 1; i <= n; i++) want[w[i]] = 1 }
    $2 != "no" && $3 == "false" && ($1 in want || $4 == "failed") { print $1 }' | xargs)"
  rm -f "$STOPPED_FILE"

  [[ -n "$names" ]] || return 0

  # shellcheck disable=SC2086
  docker start $names >/dev/null
  echo "started: $names"
  watchdog_notify "🟢 Data disk back" "$GREEN" \
    "Mount" "\`$WATCHDOG_DATA_ROOT\`" \
    "Started" "${names// /, }"
}

case "${1:-}" in
start)
  start_data_containers
  ;;
stop)
  # Docker stops everything itself on shutdown; stopping here would mark the
  # containers as manually stopped and page on every reboot.
  [[ "$(systemctl is-system-running 2>/dev/null || true)" == stopping ]] && exit 0
  stop_data_containers "unmounted, or its disk disappeared"
  ;;
boot)
  mountpoint -q "$WATCHDOG_DATA_ROOT" ||
    stop_data_containers "booted without the disk"
  ;;
hung)
  # From the heartbeat: still mounted, but direct reads keep timing out. An
  # unmounted disk is the stop path's job.
  mountpoint -q "$WATCHDOG_DATA_ROOT" || exit 0
  stop_data_containers "mounted but reads time out; power-cycle it with watchdog/scripts/recover-data-disk.sh"
  ;;
*)
  echo "usage: $0 start|stop|boot|hung" >&2
  exit 2
  ;;
esac
