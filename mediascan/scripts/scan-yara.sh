#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
MEDIASCAN_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly MEDIASCAN_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_mediascan_config

readonly RULES="${MEDIASCAN_YARA_RULES:-}"
readonly REPORT="$MEDIASCAN_REPORT_DIR/yara.jsonl"

if [[ -z "$RULES" ]]; then
  echo "MEDIASCAN_YARA_RULES is not set; skipping YARA."
  : >"$REPORT"
  exit 0
fi

if [[ ! -r "$RULES" ]]; then
  echo "Cannot read YARA rules: $RULES" >&2
  exit 1
fi

require_command yara
require_command find

readonly MAX_SIZE="${MEDIASCAN_YARA_MAX_SIZE:-${MEDIASCAN_CLAMAV_MAX_SIZE:-25M}}"
mapfile -t roots < <(read_path_list "$MEDIASCAN_DIR/conf/roots.txt")

matches="$(mktemp)"
trap 'rm -f "$matches"' EXIT

# yara exits non-zero on a rule match, so failures are collected rather than
# allowed to abort the run.
while IFS= read -r -d '' file; do
  yara -w "$RULES" "$file" 2>/dev/null || true
done < <(find "${roots[@]}" -type f -size -"$MAX_SIZE" -not -path "$MEDIASCAN_QUARANTINE/*" -print0) >"$matches"

python3 - "$matches" "$REPORT" <<'PYTHON'
import json
import sys

source, destination = sys.argv[1], sys.argv[2]
count = 0
with open(source, encoding="utf-8", errors="replace") as handle, \
     open(destination, "w", encoding="utf-8") as report:
    for line in handle:
        rule, _, path = line.rstrip("\n").partition(" ")
        if not rule or not path:
            continue
        report.write(json.dumps({"path": path, "problems": [f"MALWARE: YARA rule {rule}"]}) + "\n")
        count += 1
print(f"{count} YARA match(es) recorded in {destination}")
PYTHON
