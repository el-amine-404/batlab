#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_watchdog_config
: "${WATCHDOG_HEALTHCHECK_URL:?WATCHDOG_HEALTHCHECK_URL is not configured}"

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
run_check "sshd" sshd_answers
run_check "journald" journald_answers

if ((${#failures[@]} == 0)); then
  curl -fsS -m 10 --retry 3 -o /dev/null "$WATCHDOG_HEALTHCHECK_URL"
  exit 0
fi

printf '%s\n' "${failures[@]}" >&2
curl -fsS -m 10 --retry 3 -o /dev/null --data-raw "$(printf '%s\n' "${failures[@]}")" \
  "$WATCHDOG_HEALTHCHECK_URL/fail"
exit 1
