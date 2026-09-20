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

status=0

python3 "$SCRIPT_DIR/deep-verify.py" "${roots[@]}" \
  "${excludes[@]}" --exclude "$MEDIASCAN_QUARANTINE" \
  --marker "$MEDIASCAN_STATE_DIR/.deep-marker" \
  --report "$MEDIASCAN_REPORT_DIR/deep.jsonl" \
  --fraction "${MEDIASCAN_DEEP_FRACTION:-4}" \
  --sample-seconds "${MEDIASCAN_DEEP_SAMPLE_SECONDS:-10}" \
  --time-budget "${MEDIASCAN_DEEP_TIME_BUDGET:-7200}" \
  --max-cpu-temp "${MEDIASCAN_MAX_CPU_TEMP:-0}" \
  --cpu-temp-sensor "${MEDIASCAN_CPU_TEMP_SENSOR:-}" \
  "$@" || status=$?

# Reading a container to its end is what makes the embedded parts reachable, and
# the pass above has just done it. A container carrying nothing costs one
# ffprobe, so this walks the whole library rather than the weekly slice.
echo
echo "== Embedded content"
python3 "$SCRIPT_DIR/verify-embedded.py" "${roots[@]}" \
  "${excludes[@]}" --exclude "$MEDIASCAN_QUARANTINE" \
  --report "$MEDIASCAN_REPORT_DIR/embedded.jsonl" \
  --time-budget "${MEDIASCAN_EMBEDDED_TIME_BUDGET:-3600}" \
  --quiet || status=1

exit "$status"
