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
trap 'rm -f "$candidates" "$infected"' EXIT

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
