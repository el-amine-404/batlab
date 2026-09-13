#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
RESTIC_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly RESTIC_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_restic_config
require_command restic

healthcheck_ping start
trap 'healthcheck_ping "$?"' EXIT

"$SCRIPT_DIR/dump-databases.sh"

paths=()
while IFS= read -r path; do
  [[ -z "$path" || "$path" == \#* ]] && continue
  if [[ -e "$path" ]]; then
    paths+=("$path")
  else
    echo "Skipping missing path: $path" >&2
  fi
done <"$RESTIC_DIR/conf/paths.txt"

if ((${#paths[@]} == 0)); then
  echo "No configured backup paths exist" >&2
  exit 1
fi

# A run killed mid-way (reboot, the data disk dropping) leaves its lock behind,
# and the exclusive lock prune needs would then fail every night. Without
# --remove-all this only clears locks whose process is gone or that went stale.
restic unlock

restic backup \
  --tag automated \
  --exclude-file "$RESTIC_DIR/conf/excludes.txt" \
  "${paths[@]}"

restic forget --prune \
  --keep-daily "${RESTIC_KEEP_DAILY:-7}" \
  --keep-weekly "${RESTIC_KEEP_WEEKLY:-4}" \
  --keep-monthly "${RESTIC_KEEP_MONTHLY:-12}" \
  --keep-yearly "${RESTIC_KEEP_YEARLY:-3}"
