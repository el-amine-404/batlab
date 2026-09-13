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

exec python3 "$SCRIPT_DIR/deep-verify.py" "${roots[@]}" \
  "${excludes[@]}" --exclude "$MEDIASCAN_QUARANTINE" \
  --marker "$MEDIASCAN_STATE_DIR/.deep-marker" \
  --report "$MEDIASCAN_REPORT_DIR/deep.jsonl" \
  --fraction "${MEDIASCAN_DEEP_FRACTION:-4}" \
  --sample-seconds "${MEDIASCAN_DEEP_SAMPLE_SECONDS:-10}" \
  --time-budget "${MEDIASCAN_DEEP_TIME_BUDGET:-7200}" \
  --max-cpu-temp "${MEDIASCAN_MAX_CPU_TEMP:-0}" \
  --cpu-temp-sensor "${MEDIASCAN_CPU_TEMP_SENSOR:-}" \
  "$@"
