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
require_command ffprobe

mapfile -t roots < <(read_path_list "$MEDIASCAN_DIR/conf/roots.txt")
mapfile -t excludes < <(read_exclude_list "$MEDIASCAN_DIR/conf/excludes.txt")

ignore_arguments=()
for suffix in ${MEDIASCAN_IGNORE_SUFFIXES:-}; do
  ignore_arguments+=(--ignore-suffix "$suffix")
done

readonly TYPES_REPORT="$MEDIASCAN_REPORT_DIR/types.jsonl"
readonly MEDIA_REPORT="$MEDIASCAN_REPORT_DIR/media.jsonl"
readonly SUBTITLES_REPORT="$MEDIASCAN_REPORT_DIR/subtitles.jsonl"
readonly CLAMAV_REPORT="$MEDIASCAN_REPORT_DIR/clamav.jsonl"
readonly YARA_REPORT="$MEDIASCAN_REPORT_DIR/yara.jsonl"
readonly VIRUSTOTAL_REPORT="$MEDIASCAN_REPORT_DIR/virustotal.jsonl"

flagged=0
summary_arguments=()

run_check() {
  local key="$1" label="$2" report="$3" status=0
  shift 3
  echo
  echo "== $label"
  # Do not summarize or quarantine yesterday's results if a scanner fails
  # before producing today's report.
  : >"$report"
  "$@" || status=$?
  summary_arguments+=(--check "$key=$status")
  if ((status)); then
    flagged=1
  fi
}

run_check types "File types" "$TYPES_REPORT" python3 "$SCRIPT_DIR/verify-types.py" "${roots[@]}" \
  "${excludes[@]}" "${ignore_arguments[@]}" --exclude "$MEDIASCAN_QUARANTINE" \
  --report "$TYPES_REPORT" --quiet

run_check media "Video containers" "$MEDIA_REPORT" python3 "$SCRIPT_DIR/verify-media.py" "${roots[@]}" \
  "${excludes[@]}" --exclude "$MEDIASCAN_QUARANTINE" \
  --report "$MEDIA_REPORT" --quiet

run_check subtitles "Subtitles" "$SUBTITLES_REPORT" python3 "$SCRIPT_DIR/verify-subtitles.py" "${roots[@]}" \
  "${excludes[@]}" --exclude "$MEDIASCAN_QUARANTINE" \
  --report "$SUBTITLES_REPORT" --quiet

run_check clamav "ClamAV" "$CLAMAV_REPORT" "$SCRIPT_DIR/scan-clamav.sh"
if [[ -n "${MEDIASCAN_YARA_RULES:-}" ]]; then
  run_check yara "YARA" "$YARA_REPORT" "$SCRIPT_DIR/scan-yara.sh"
else
  : >"$YARA_REPORT"
  summary_arguments+=(--check yara=skipped)
  echo "YARA: skipped (no rules configured)."
fi

# The example config ships a your_*_here placeholder; with it every lookup fails
# with 401 after a 16 s pause each, burning hours for nothing.
if [[ -n "${VT_API_KEY:-}" && "$VT_API_KEY" != your_*_here ]]; then
  run_check virustotal "VirusTotal" "$VIRUSTOTAL_REPORT" python3 "$SCRIPT_DIR/scan-virustotal.py" "${roots[@]}" \
    "${excludes[@]}" --exclude "$MEDIASCAN_QUARANTINE" \
    --report "$VIRUSTOTAL_REPORT" --cache "$MEDIASCAN_STATE_DIR/virustotal-cache.json" \
    --max-lookups "${MEDIASCAN_VT_MAX_LOOKUPS:-400}" --quiet
else
  : >"$VIRUSTOTAL_REPORT"
  summary_arguments+=(--check virustotal=skipped)
fi

# Verdicts that mean the file has no business being in the library at all, as
# opposed to being merely unusual.
quarantine_arguments=(
  --problem MALWARE --problem EXECUTABLE --problem SCRIPT --problem ARCHIVE
  --problem ACTIVE_CONTENT --problem EMBEDDED --problem BINARY
  --problem NOT_SUBTITLE --problem ATTACHMENT --problem NOT_VIDEO
  --problem DANGEROUS_NAME
)
if [[ "${MEDIASCAN_QUARANTINE_APPLY:-0}" == "1" ]]; then
  quarantine_arguments+=(--apply)
  summary_arguments+=(--quarantine enabled)
fi

# One pass over the merged reports: a file caught by several engines is moved
# once, and the hardlink index is built once instead of per report.
merged="$(mktemp)"
trap 'rm -f "$merged"' EXIT
cat "$CLAMAV_REPORT" "$YARA_REPORT" "$VIRUSTOTAL_REPORT" "$TYPES_REPORT" \
  "$SUBTITLES_REPORT" "$MEDIA_REPORT" 2>/dev/null >"$merged" || true

quarantine_status=0
if [[ -s "$merged" ]]; then
  echo
  echo "== Quarantine"
  python3 "$SCRIPT_DIR/quarantine.py" \
    --root "$MEDIASCAN_LIBRARY_ROOT" \
    --quarantine "$MEDIASCAN_QUARANTINE" \
    --scan-root "$MEDIASCAN_LIBRARY_ROOT" \
    --from-jsonl "$merged" \
    "${quarantine_arguments[@]}" || quarantine_status=$?
fi

echo
if ! summary="$(python3 "$SCRIPT_DIR/summarize-sweep.py" --report-dir "$MEDIASCAN_REPORT_DIR" \
  "${summary_arguments[@]}" --quarantine-status "$quarantine_status")"; then
  flagged=1
fi
if [[ -z "$summary" ]]; then
  summary="Report summary failed. Inspect journalctl -u batlab-mediascan-sweep.service and $MEDIASCAN_REPORT_DIR"
  flagged=1
fi
printf '%s\n' "$summary"
if ((flagged)); then
  echo "Sweep finished with findings. Reports are in $MEDIASCAN_REPORT_DIR."
  mediascan_notify "Media scan: findings on $(hostname)" \
    "$summary"
else
  echo "Sweep finished clean."
fi

exit "$flagged"
