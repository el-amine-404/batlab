#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
MEDIASCAN_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly MEDIASCAN_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_mediascan_config
require_command python3
require_command ffmpeg

mapfile -t roots < <(read_path_list "$MEDIASCAN_DIR/conf/roots.txt")
mapfile -t excludes < <(read_exclude_list "$MEDIASCAN_DIR/conf/excludes.txt")

deep_status=0
embedded_status=0

python3 "$SCRIPT_DIR/deep-verify.py" "${roots[@]}" \
  "${excludes[@]}" --exclude "$MEDIASCAN_QUARANTINE" \
  --marker "$MEDIASCAN_STATE_DIR/.deep-marker" \
  --report "$MEDIASCAN_REPORT_DIR/deep.jsonl" \
  --fraction "${MEDIASCAN_DEEP_FRACTION:-4}" \
  --sample-seconds "${MEDIASCAN_DEEP_SAMPLE_SECONDS:-10}" \
  --time-budget "${MEDIASCAN_DEEP_TIME_BUDGET:-7200}" \
  --max-cpu-temp "${MEDIASCAN_MAX_CPU_TEMP:-0}" \
  --cpu-temp-sensor "${MEDIASCAN_CPU_TEMP_SENSOR:-}" \
  "$@" || deep_status=$?

# Reading a container to its end is what makes the embedded parts reachable, and
# the pass above has just done it. A container carrying nothing costs one
# ffprobe, so this walks the whole library rather than the weekly slice.
echo
echo "== Embedded content"
python3 "$SCRIPT_DIR/verify-embedded.py" "${roots[@]}" \
  "${excludes[@]}" --exclude "$MEDIASCAN_QUARANTINE" \
  --report "$MEDIASCAN_REPORT_DIR/embedded.jsonl" \
  --state "$MEDIASCAN_STATE_DIR/embedded-state.json" \
  --time-budget "${MEDIASCAN_EMBEDDED_TIME_BUDGET:-3600}" \
  --quiet || embedded_status=$?

if ((deep_status > 1 || embedded_status > 1)); then
  mediascan_notify "Deep media scan failed on $(hostname)" \
    "Decode status: $deep_status
Embedded status: $embedded_status
Reports: $MEDIASCAN_REPORT_DIR"
  exit 2
fi
if ((deep_status == 1 || embedded_status == 1)); then
  mediascan_notify "Deep media scan: findings on $(hostname)" \
    "Decode status: $deep_status
Embedded status: $embedded_status
Reports: $MEDIASCAN_REPORT_DIR"
  exit 1
fi
exit 0
