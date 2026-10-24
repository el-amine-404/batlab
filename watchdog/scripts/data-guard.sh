#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_watchdog_config
: "${WATCHDOG_DATA_ROOT:?the data guard needs WATCHDOG_DATA_ROOT}"

readonly STATE_DIR="${WATCHDOG_STATE_DIR:-/var/lib/batlab-watchdog}"
readonly STOPPED_FILE="$STATE_DIR/stopped-containers"
readonly START_FAILURE_FILE="$STATE_DIR/container-start-failures"
# When the disk went: "back" and "revived" cards say for how long.
readonly DOWN_FILE="$STATE_DIR/data-disk-down"

exec 9>"${WATCHDOG_LOCK_FILE:-/run/batlab-data-guard.lock}"
flock 9

# name, restart policy, running, whether the last start failed; only for
# containers bind-mounting something under the data root.
data_containers() {
  docker ps -aq |
    xargs -r docker inspect -f '{{.Name}} {{or .HostConfig.RestartPolicy.Name "no"}} {{.State.Running}} {{if .State.Error}}failed{{else}}ok{{end}}{{range .Mounts}} {{.Source}}{{end}}' |
    awk -v d="$WATCHDOG_DATA_ROOT" '{ for (i = 5; i <= NF; i++) if ($i == d || index($i, d "/") == 1) { print substr($1, 2), $2, $3, $4; next } }'
}

stop_data_containers() {
  local reason="$1" names log count fields
  names="$(data_containers | awk '$3 == "true" { print $1 }' | xargs)"
  count="$(wc -w <<<"$names")"
  log="$(data_disk_log || true)"

  echo "$reason; stopping: ${names:-none}"
  # Before stopping: a container stuck on a hung disk can keep docker stop
  # waiting, and the card must go out regardless.
  install -d -m 750 "$STATE_DIR"
  [[ -e "$DOWN_FILE" ]] || date +%s >"$DOWN_FILE"
  fields=( \
    "Disk" "$(data_disk_label)" \
    "Mount" "\`$WATCHDOG_DATA_ROOT\`" \
    "Why" "$reason" \
    "Stopped" "${count}: ${names// /, }" )
  [[ -n "$log" ]] && fields+=("Log" "$(code_block "$log")")
  fields+=( \
    "Recovery" "automatic USB 2 recovery retries every 2 min" \
    "Action" "if no recovery card arrives within 10 min, replug enclosure power and USB" )
  watchdog_notify "Data disk unavailable" failure "${fields[@]}"

  if [[ -n "$names" ]]; then
    install -d -m 750 "$STATE_DIR"
    tr ' ' '\n' <<<"$names" >>"$STOPPED_FILE"
    # shellcheck disable=SC2086
    docker stop $names >/dev/null
  fi
}

start_data_containers() {
  local wanted="" names name running="" remaining="" speed=""

  if ! mountpoint -q "$WATCHDOG_DATA_ROOT"; then
    echo "$WATCHDOG_DATA_ROOT is not mounted" >&2
    exit 1
  fi

  if [[ -r "$STOPPED_FILE" ]]; then
    wanted="$(sort -u "$STOPPED_FILE" | xargs)"
  fi

  # Only containers this guard stopped, or that Docker failed to start while the
  # disk was missing. Containers stopped on purpose stay stopped.
  names="$(data_containers | awk -v wanted="$wanted" '
    BEGIN { n = split(wanted, w, " "); for (i = 1; i <= n; i++) want[w[i]] = 1 }
    $2 != "no" && $3 == "false" && ($1 in want || $4 == "failed") { print $1 }' | xargs)"
  for name in $names; do
    if docker start "$name" >/dev/null &&
      [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" == true ]]; then
      running+="${running:+ }$name"
    fi
  done

  # Keep recovery intent until every container is confirmed running. A USB
  # disconnect in the middle of `docker start a b c ...` used to erase this
  # file after only the first few containers had started.
  for name in $wanted; do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)" != true ]]; then
      remaining+="${remaining:+ }$name"
    fi
  done
  if [[ -n "$remaining" ]]; then
    tr ' ' '\n' <<<"$remaining" >"$STOPPED_FILE"
  else
    rm -f "$STOPPED_FILE"
  fi

  [[ -n "$running" ]] && echo "started: $running"
  if [[ -n "$remaining" ]]; then
    if [[ ! -r "$START_FAILURE_FILE" ]] || [[ "$(<"$START_FAILURE_FILE")" != "$remaining" ]]; then
      printf '%s\n' "$remaining" >"$START_FAILURE_FILE"
      watchdog_notify "Data disk recovery incomplete" failure \
        "Disk" "$(data_disk_label)" \
        "Mount" "\`$WATCHDOG_DATA_ROOT\`" \
        "Still stopped" "${remaining// /, }" \
        "Issue" "containers did not all restart after the disk returned" \
        "Fix" "the data-disk revival timer retries every 2 min; inspect: docker logs <container>"
    fi
    return 1
  fi
  rm -f "$START_FAILURE_FILE"

  [[ -n "$wanted" ]] || return 0

  local down=""
  [[ -r "$DOWN_FILE" ]] && down="$(since "$(<"$DOWN_FILE")")"
  speed="$(data_disk_usb_speed 2>/dev/null || true)"
  rm -f "$DOWN_FILE"
  watchdog_notify "Data disk back" success \
    "Disk" "$(data_disk_label)" \
    "Mount" "\`$WATCHDOG_DATA_ROOT\`" \
    "Down for" "${down:-unknown}" \
    "USB link" "${speed:-unknown} Mbit/s" \
    "Restarted" "${wanted// /, }"
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
