#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
MEDIASCAN_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly MEDIASCAN_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_mediascan_config
require_command inotifywait
require_command python3

mapfile -t roots < <(read_path_list "$MEDIASCAN_DIR/conf/roots.txt")
mapfile -t excludes < <(read_exclude_list "$MEDIASCAN_DIR/conf/excludes.txt")

ignore_arguments=()
for suffix in ${MEDIASCAN_IGNORE_SUFFIXES:-}; do
  ignore_arguments+=(--ignore-suffix "$suffix")
done

# Directories the sweep skips must not be watched either, or the watcher
# reintroduces exactly the false positives the exclude list exists to prevent.
exclude_pattern="$MEDIASCAN_QUARANTINE"
for index in "${!excludes[@]}"; do
  [[ "${excludes[$index]}" == "--exclude" ]] && continue
  exclude_pattern+="|${excludes[$index]}"
done

readonly WATCH_REPORT_DIR="$MEDIASCAN_REPORT_DIR/watch"
install -d -m 750 "$WATCH_REPORT_DIR"

scan_one() {
  local path="$1"
  [[ -f "$path" ]] || return 0

  local stamp report status=0
  stamp="$(date +%s%N)"
  report="$WATCH_REPORT_DIR/$stamp.jsonl"
  : >"$report"

  python3 "$SCRIPT_DIR/verify-types.py" "$path" "${ignore_arguments[@]}" \
    --report "$report.types" --quiet || status=1
  python3 "$SCRIPT_DIR/verify-media.py" "$path" --report "$report.media" --quiet || status=1
  python3 "$SCRIPT_DIR/verify-subtitles.py" "$path" --report "$report.subs" --quiet || status=1

  cat "$report".{types,media,subs} 2>/dev/null >"$report" || true
  rm -f "$report".{types,media,subs}

  if ((status == 0)); then
    rm -f "$report"
    return 0
  fi

  echo "Flagged: $path"

  local quarantine_arguments=(
    --problem MALWARE --problem EXECUTABLE --problem SCRIPT --problem ARCHIVE
    --problem DANGEROUS_NAME --problem ACTIVE_CONTENT --problem EMBEDDED
    --problem BINARY --problem NOT_SUBTITLE --problem ATTACHMENT --problem NOT_VIDEO
  )
  [[ "${MEDIASCAN_QUARANTINE_APPLY:-0}" == "1" ]] && quarantine_arguments+=(--apply)

  python3 "$SCRIPT_DIR/quarantine.py" \
    --root "$MEDIASCAN_LIBRARY_ROOT" \
    --quarantine "$MEDIASCAN_QUARANTINE" \
    --scan-root "$MEDIASCAN_LIBRARY_ROOT" \
    --from-jsonl "$report" \
    "${quarantine_arguments[@]}" || true
}

echo "Watching: ${roots[*]}"
echo "Excluding: $exclude_pattern"

# close_write is a finished write; moved_to is qBittorrent's atomic move out of
# the incomplete directory.
inotifywait -m -r -q \
  -e close_write -e moved_to \
  --exclude "$exclude_pattern" \
  --format '%w%f' \
  "${roots[@]}" |
  while IFS= read -r path; do
    scan_one "$path" || true
  done
