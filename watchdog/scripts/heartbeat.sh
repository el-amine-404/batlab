#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_watchdog_config
: "${WATCHDOG_HEALTHCHECK_URL:?WATCHDOG_HEALTHCHECK_URL is not configured}"

readonly STATE_DIR="/var/lib/batlab-watchdog"
readonly FAILING_FILE="$STATE_DIR/heartbeat-failing"
readonly DISK_FAILS_FILE="$STATE_DIR/data-disk-failures"
readonly RED=15158332
readonly GREEN=3066993

failures=()

# A process stuck in uninterruptible I/O ignores SIGKILL, so waiting on a hung
# check would hang the heartbeat too. It is left behind and reported instead.
run_check() {
  local name="$1"
  shift

  "$@" >/dev/null 2>&1 &
  local pid=$! deadline=$((SECONDS + ${WATCHDOG_CHECK_TIMEOUT:-10}))

  while kill -0 "$pid" 2>/dev/null; do
    if ((SECONDS >= deadline)); then
      failures+=("$name did not answer within ${WATCHDOG_CHECK_TIMEOUT:-10}s")
      return 0
    fi
    sleep 0.5
  done

  wait "$pid" || failures+=("$name failed")
}

# Reads the block device with O_DIRECT: statfs and directory listings are served
# from cache and kept answering all week while the disk was gone.
data_disk_readable() {
  local device
  mountpoint -q "$WATCHDOG_DATA_ROOT" || return 1
  device="$(findmnt -no SOURCE "$WATCHDOG_DATA_ROOT")"
  dd if="$device" of=/dev/null bs=4096 count=1 skip=$((RANDOM * 64)) iflag=direct status=none
}

sshd_answers() {
  exec 3<>"/dev/tcp/127.0.0.1/${WATCHDOG_SSH_PORT:-22}"
  printf 'SSH-2.0-batlab-watchdog\r\n' >&3
  head -c 4 <&3 | grep -q '^SSH-'
}

# With a second way out (lab2's Wi-Fi backup) every other check still passes
# when the cable is pulled, and the heartbeat would stay green while the house
# cannot reach this host. Reading carrier fails outright on a downed interface.
cable_connected() {
  [[ "$(<"/sys/class/net/$WATCHDOG_WIRED_IFACE/carrier")" == 1 ]]
}

journald_answers() {
  journalctl --sync
}

# A full answer, not just a reply: AdGuard alone replies SERVFAIL when Unbound
# behind it is down, and the house has no working DNS either way.
dns_answers() {
  python3 - "$WATCHDOG_DNS_SERVER" "${WATCHDOG_DNS_NAME:-debian.org}" <<'PY'
import random, socket, struct, sys
server, name = sys.argv[1], sys.argv[2]
qid = random.randrange(65536)
query = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
query += b"".join(bytes([len(label)]) + label.encode() for label in name.rstrip(".").split("."))
query += b"\0" + struct.pack(">HH", 1, 1)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(5)
sock.sendto(query, (server, 53))
reply = sock.recv(4096)
rid, flags, _, answers = struct.unpack(">HHHH", reply[:8])
sys.exit(0 if rid == qid and flags & 0x8000 and flags & 0xF == 0 and answers > 0 else 1)
PY
}

if [[ -n "$WATCHDOG_DATA_ROOT" ]]; then
  run_check "data disk" data_disk_readable
fi
if [[ -n "${WATCHDOG_DNS_SERVER:-}" ]]; then
  run_check "dns" dns_answers
fi
if [[ -n "${WATCHDOG_WIRED_IFACE:-}" ]]; then
  run_check "network cable ($WATCHDOG_WIRED_IFACE)" cable_connected
fi
run_check "sshd" sshd_answers
run_check "journald" journald_answers

install -d -m 750 "$STATE_DIR"

# A Discord card on the first failed run and on the first good one after it, so
# a failure reaches the channel even if healthchecks.io is not set to notify.
if ((${#failures[@]})) && [[ ! -e "$FAILING_FILE" ]]; then
  printf '%s\n' "${failures[@]}" >"$FAILING_FILE"
  watchdog_notify "🔴 Self-test failing" "$RED" "Failed" "$(printf '%s\n' "${failures[@]}")"
elif ((${#failures[@]} == 0)) && [[ -e "$FAILING_FILE" ]]; then
  watchdog_notify "🟢 Self-test passing again" "$GREEN" "Was failing" "$(<"$FAILING_FILE")"
  rm -f "$FAILING_FILE"
fi

# A disk that stays mounted but stops answering never unmounts, so the data
# guard does not see it. After WATCHDOG_DISK_HUNG_AFTER failed runs in a row it
# is told directly; in its own unit, because docker stop can hang on the disk.
if [[ -n "$WATCHDOG_DATA_ROOT" ]]; then
  disk_fails=0
  [[ -r "$DISK_FAILS_FILE" ]] && disk_fails="$(<"$DISK_FAILS_FILE")"
  if [[ " ${failures[*]} " == *" data disk "* ]]; then
    disk_fails=$((disk_fails + 1))
    if ((disk_fails == ${WATCHDOG_DISK_HUNG_AFTER:-2})); then
      systemd-run --no-block --collect --unit="batlab-data-guard-hung-$(date +%s)" \
        "$SCRIPT_DIR/data-guard.sh" hung || true
    fi
  else
    # Back by itself, without a remount: restart what the guard stopped.
    if ((disk_fails >= ${WATCHDOG_DISK_HUNG_AFTER:-2})); then
      systemd-run --no-block --collect --unit="batlab-data-guard-back-$(date +%s)" \
        "$SCRIPT_DIR/data-guard.sh" start || true
    fi
    disk_fails=0
  fi
  echo "$disk_fails" >"$DISK_FAILS_FILE"
fi

if ((${#failures[@]} == 0)); then
  curl -fsS -m 10 --retry 3 -o /dev/null "$WATCHDOG_HEALTHCHECK_URL"
  exit 0
fi

printf '%s\n' "${failures[@]}" >&2
curl -fsS -m 10 --retry 3 -o /dev/null --data-raw "$(printf '%s\n' "${failures[@]}")" \
  "$WATCHDOG_HEALTHCHECK_URL/fail"
exit 1
