#!/usr/bin/env bash

set -Eeuo pipefail

readonly DEFAULT_UPLOAD_DIR="/mnt/storage/data/immich/upload"
readonly DEFAULT_LIBRARY_DIR="/mnt/storage/data/immich/library/admin"
readonly DEFAULT_PROGRESS_INTERVAL=2

upload_dir="$DEFAULT_UPLOAD_DIR"
library_dir="$DEFAULT_LIBRARY_DIR"
progress_interval="$DEFAULT_PROGRESS_INTERVAL"
active_child_pid=""
work_dir=""

usage() {
  cat <<'EOF'
Usage:
  check-upload-duplicates.sh [OPTIONS]
  check-upload-duplicates.sh [UPLOAD_DIR [LIBRARY_DIR]]

Check whether files left in Immich's upload staging directory already exist
byte-for-byte in the managed library. The script is read-only.

Options:
  --upload-dir PATH         Upload staging directory
  --library-dir PATH        Finalized Immich library directory
  --progress-interval SEC   Comparison progress refresh interval (default: 2)
  -h, --help                Show this help

Exit status:
  0  Check completed, including when unmatched files are found
  1  Invalid arguments or missing/unreadable directories
  2  One or more files could not be compared
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup_runtime() {
  if [[ -n "$active_child_pid" ]] && kill -0 "$active_child_pid" 2>/dev/null; then
    kill "$active_child_pid" 2>/dev/null || true
    wait "$active_child_pid" 2>/dev/null || true
  fi

  if [[ -n "$work_dir" && -d "$work_dir" ]]; then
    rm -rf -- "$work_dir"
  fi
}

handle_signal() {
  printf '\nInterrupted; stopping the active operation. No files were changed.\n' >&2
  cleanup_runtime
  exit 130
}

trap handle_signal INT TERM
trap cleanup_runtime EXIT

positional=()
while (($# > 0)); do
  case "$1" in
    --upload-dir)
      (($# >= 2)) || die '--upload-dir requires a path'
      upload_dir="$2"
      shift 2
      ;;
    --library-dir)
      (($# >= 2)) || die '--library-dir requires a path'
      library_dir="$2"
      shift 2
      ;;
    --progress-interval)
      (($# >= 2)) || die '--progress-interval requires a number of seconds'
      progress_interval="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      positional+=("$@")
      break
      ;;
    -*)
      die "unknown option: $1"
      ;;
    *)
      positional+=("$1")
      shift
      ;;
  esac
done

((${#positional[@]} <= 2)) || die 'expected at most UPLOAD_DIR and LIBRARY_DIR'
if ((${#positional[@]} >= 1)); then
  upload_dir="${positional[0]}"
fi
if ((${#positional[@]} == 2)); then
  library_dir="${positional[1]}"
fi

[[ "$progress_interval" =~ ^[1-9][0-9]*$ ]] || \
  die '--progress-interval must be a positive integer'

for command_name in cmp find stat awk mktemp; do
  command -v "$command_name" >/dev/null 2>&1 || \
    die "required command is not installed: $command_name"
done

[[ -d "$upload_dir" ]] || die "upload directory does not exist: $upload_dir"
[[ -r "$upload_dir" && -x "$upload_dir" ]] || \
  die "upload directory is not readable: $upload_dir"
[[ -d "$library_dir" ]] || die "library directory does not exist: $library_dir"
[[ -r "$library_dir" && -x "$library_dir" ]] || \
  die "library directory is not readable: $library_dir"

human_size() {
  local bytes="$1"

  if command -v numfmt >/dev/null 2>&1; then
    numfmt --to=iec-i --suffix=B "$bytes"
  else
    printf '%s bytes\n' "$bytes"
  fi
}

format_duration() {
  local total_seconds="$1"
  printf '%02d:%02d:%02d' \
    "$((total_seconds / 3600))" \
    "$(((total_seconds % 3600) / 60))" \
    "$((total_seconds % 60))"
}

print_live_status() {
  local message="$1"
  local elapsed="$2"

  if [[ -t 1 ]]; then
    printf '\r\033[2K%s' "$message"
  elif ((elapsed == 0 || elapsed % 30 < progress_interval)); then
    printf '%s\n' "$message"
  fi
}

build_library_index() {
  local index_file="$1"
  local started elapsed index_bytes message status

  printf '\nIndexing the library once (file names and sizes only)...\n'
  started="$SECONDS"
  find "$library_dir" -xdev -type f -printf '%s\t%p\0' >"$index_file" &
  active_child_pid=$!

  while kill -0 "$active_child_pid" 2>/dev/null; do
    elapsed=$((SECONDS - started))
    index_bytes="$(stat -c '%s' -- "$index_file" 2>/dev/null || printf '0')"
    message="  indexing: elapsed=$(format_duration "$elapsed"), index=$(human_size "$index_bytes")"
    print_live_status "$message" "$elapsed"
    sleep "$progress_interval"
  done

  if wait "$active_child_pid"; then
    status=0
  else
    status=$?
  fi
  active_child_pid=""

  if [[ -t 1 ]]; then
    printf '\r\033[2K'
  fi
  ((status == 0)) || die "could not index the library (find status $status)"

  index_bytes="$(stat -c '%s' -- "$index_file")"
  printf 'Library index ready in %s (%s).\n' \
    "$(format_duration "$((SECONDS - started))")" \
    "$(human_size "$index_bytes")"
}

comparison_progress() {
  local pid="$1"
  local size="$2"
  local started="$3"
  local elapsed rchar compared percent message

  elapsed=$((SECONDS - started))
  message="      elapsed=$(format_duration "$elapsed")"

  if [[ -r "/proc/$pid/io" ]]; then
    rchar="$(awk '$1 == "rchar:" {print $2}' "/proc/$pid/io" 2>/dev/null || true)"
    if [[ "$rchar" =~ ^[0-9]+$ ]]; then
      # cmp reads both inputs, so half its read characters approximate the
      # number of bytes compared from either file.
      compared=$((rchar / 2))
      ((compared > size)) && compared="$size"
      percent=$((size > 0 ? compared * 100 / size : 100))
      message+=" approximately=${percent}% ($(human_size "$compared")/$(human_size "$size"))"
    fi
  fi

  print_live_status "$message" "$elapsed"
}

compare_files() {
  local staged_file="$1"
  local candidate="$2"
  local size="$3"
  local started status

  started="$SECONDS"
  cmp --silent -- "$staged_file" "$candidate" &
  active_child_pid=$!

  comparison_progress "$active_child_pid" "$size" "$started"
  while kill -0 "$active_child_pid" 2>/dev/null; do
    sleep "$progress_interval"
    kill -0 "$active_child_pid" 2>/dev/null || break
    comparison_progress "$active_child_pid" "$size" "$started"
  done

  if wait "$active_child_pid"; then
    status=0
  else
    status=$?
  fi
  active_child_pid=""

  if [[ -t 1 ]]; then
    printf '\r\033[2K'
  fi
  printf '      completed in %s\n' "$(format_duration "$((SECONDS - started))")"

  return "$status"
}

printf 'Immich interrupted-upload duplicate check\n'
printf '  Upload staging: %s\n' "$upload_dir"
printf '  Final library:  %s\n' "$library_dir"
printf '  Mode:           read-only, exact byte comparison\n'
printf '\nScanning the upload staging directory...\n'

mapfile -d '' -t staged_files < <(
  find "$upload_dir" -xdev -type f ! -name '.immich' -print0
)

if ((${#staged_files[@]} == 0)); then
  printf 'No staged files were found. Nothing to check.\n'
  exit 0
fi

printf 'Found %d staged file(s).\n' "${#staged_files[@]}"

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/immich-duplicate-check.XXXXXXXX")"
library_index="$work_dir/library-index"
build_library_index "$library_index"

checked=0
duplicates=0
unmatched=0
comparison_errors=0

for staged_file in "${staged_files[@]}"; do
  checked=$((checked + 1))
  size="$(stat -c '%s' -- "$staged_file")"

  printf '\n[%d/%d] %s\n' "$checked" "${#staged_files[@]}" "$staged_file"
  printf '    Size: %s (%s bytes)\n' "$(human_size "$size")" "$size"
  printf '    Searching the library index for same-sized candidates...\n'

  mapfile -d '' -t candidates < <(
    while IFS= read -r -d '' index_record; do
      indexed_size="${index_record%%$'\t'*}"
      if [[ "$indexed_size" == "$size" ]]; then
        printf '%s\0' "${index_record#*$'\t'}"
      fi
    done <"$library_index"
  )

  if ((${#candidates[@]} == 0)); then
    printf '    NOT FOUND: no library file has the same byte size\n'
    unmatched=$((unmatched + 1))
    continue
  fi

  printf '    Found %d same-sized candidate(s).\n' "${#candidates[@]}"
  matched=false
  candidate_number=0

  for candidate in "${candidates[@]}"; do
    candidate_number=$((candidate_number + 1))
    printf '    Candidate %d/%d: %s\n' \
      "$candidate_number" "${#candidates[@]}" "$candidate"

    if compare_files "$staged_file" "$candidate" "$size"; then
      printf '    EXACT DUPLICATE: %s\n' "$candidate"
      duplicates=$((duplicates + 1))
      matched=true
      break
    else
      status=$?
      if ((status > 1)); then
        printf '    ERROR: cmp could not read the candidate (status %d)\n' \
          "$status" >&2
        comparison_errors=$((comparison_errors + 1))
      else
        printf '      different contents\n'
      fi
    fi
  done

  if [[ "$matched" == false ]]; then
    printf '    NOT FOUND: same-sized candidates contain different bytes\n'
    unmatched=$((unmatched + 1))
  fi
done

printf '\nSummary\n'
printf '  Staged files checked: %d\n' "$checked"
printf '  Exact duplicates:     %d\n' "$duplicates"
printf '  Not in library:       %d\n' "$unmatched"
printf '  Comparison errors:    %d\n' "$comparison_errors"

if ((unmatched > 0)); then
  printf '\nKeep every unmatched file; it is not an exact copy of a library file.\n'
fi

if ((comparison_errors > 0)); then
  exit 2
fi
