#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
MEDIASCAN_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly MEDIASCAN_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_mediascan_config
require_command clamdscan
require_command find

# clamd refuses anything above MaxFileSize (25M by default on this host) and
# says nothing when it skips, so feeding it whole films only hides that fact.
readonly MAX_SIZE="${MEDIASCAN_CLAMAV_MAX_SIZE:-25M}"
readonly MARKER="$MEDIASCAN_STATE_DIR/.clamav-marker"
readonly REPORT="$MEDIASCAN_REPORT_DIR/clamav.jsonl"

mapfile -t roots < <(read_path_list "$MEDIASCAN_DIR/conf/roots.txt")

candidates="$(mktemp)"
infected="$(mktemp)"
selftest="$(mktemp -d)"
trap 'rm -rf "$candidates" "$infected" "$selftest"' EXIT

# An engine that cannot read the files it is handed reports every one of them
# as clean, which looks identical to a successful scan. Prove detection works
# on a known positive before trusting a clean result.
# shellcheck disable=SC2016  # the EICAR string is literal; $EICAR must not expand
printf '%s' 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' >"$selftest/eicar.com"
chmod 0644 "$selftest/eicar.com"
# clamdscan reports a detection with exit status 1, which pipefail would turn
# into a pipeline failure, so the result is captured before it is examined.
selftest_output="$(clamdscan --fdpass --no-summary --infected "$selftest/eicar.com" 2>/dev/null || true)"
if ! grep -q "FOUND" <<<"$selftest_output"; then
  echo "clamd did not detect the EICAR test file; refusing to report a clean scan." >&2
  echo "  Check that clamav-daemon is running and can read through --fdpass." >&2
  exit 2
fi

find_arguments=("${roots[@]}" -type f -size -"$MAX_SIZE" -not -path "$MEDIASCAN_QUARANTINE/*")
if [[ "${MEDIASCAN_CLAMAV_INCREMENTAL:-1}" == "1" && -f "$MARKER" ]]; then
  find_arguments+=(-newer "$MARKER")
fi

find "${find_arguments[@]}" -print >"$candidates"
count="$(wc -l <"$candidates")"

if ((count == 0)); then
  echo "No files under $MAX_SIZE to scan."
  : >"$REPORT"
  touch "$MARKER"
  exit 0
fi

echo "Scanning $count file(s) under $MAX_SIZE with clamd."

status=0
clamdscan --fdpass --multiscan --infected --no-summary --file-list="$candidates" >"$infected" || status=$?

if ((status > 1)); then
  echo "clamdscan failed with status $status" >&2
  exit "$status"
fi

python3 - "$infected" "$REPORT" <<'PYTHON'
import json
import sys

source, destination = sys.argv[1], sys.argv[2]
count = 0
with open(source, encoding="utf-8", errors="replace") as handle, \
     open(destination, "w", encoding="utf-8") as report:
    for line in handle:
        line = line.rstrip("\n")
        if not line.endswith(" FOUND"):
            continue
        path, _, signature = line[: -len(" FOUND")].rpartition(": ")
        report.write(json.dumps({"path": path, "problems": [f"MALWARE: {signature}"]}) + "\n")
        count += 1
print(f"{count} infected file(s) recorded in {destination}")
PYTHON

touch "$MARKER"
exit "$status"
