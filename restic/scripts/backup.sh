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

started="$(date +%s)"
stage="initialization"
backup_log="$(mktemp)"
finish() {
  local status="$1" elapsed="$(($(date +%s) - started))"
  rm -f -- "$backup_log"
  healthcheck_ping "$status"
  if ((status)); then
    restic_notify failure "Backup failed" \
      "Issue" "$stage failed with exit status $status" \
      "Impact" "The newest off-site snapshot may be missing or incomplete" \
      "Fix" "Inspect: journalctl -u batlab-restic-backup -n 100; retry: sudo systemctl start batlab-restic-backup" \
      "Duration" "${elapsed}s" || echo "Backup failure notification was not delivered" >&2
  fi
}

healthcheck_ping start
trap 'finish "$?"' EXIT

stage="PostgreSQL exports"
"$SCRIPT_DIR/dump-databases.sh"

# Whole copies of the SQLite databases (dump-sqlite.py). One that fails keeps
# its last good copy; the snapshot still runs, and the run then fails.
sqlite_failed=0
stage="SQLite exports"
"$SCRIPT_DIR/dump-sqlite.py" || sqlite_failed=1

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
stage="repository unlock"
restic unlock

# --retry-lock: the weekly check may be reading the repository (check.sh).
stage="snapshot upload"
restic backup --retry-lock 2h \
  --tag automated \
  --exclude-file "$RESTIC_DIR/conf/excludes.txt" \
  "${paths[@]}" | tee "$backup_log"

stage="retention and prune"
restic forget --prune --retry-lock 2h \
  --keep-daily "${RESTIC_KEEP_DAILY:-7}" \
  --keep-weekly "${RESTIC_KEEP_WEEKLY:-4}" \
  --keep-monthly "${RESTIC_KEEP_MONTHLY:-12}" \
  --keep-yearly "${RESTIC_KEEP_YEARLY:-3}"

if ((sqlite_failed)); then
  stage="one or more SQLite exports"
  echo "Some SQLite databases weren't copied (see above): their last good copies are in the snapshot" >&2
  exit 1
fi

snapshot="$(sed -nE 's/^snapshot ([0-9a-f]+) saved$/\1/p' "$backup_log" | tail -1)"
elapsed="$(($(date +%s) - started))"
stage="success notification"
restic_notify success "Backup completed" \
  "Snapshot" "${snapshot:-saved; see journal for ID}" \
  "Databases" "Paperless and Immich PostgreSQL; all SQLite databases validated" \
  "Scope" "Container state, host configuration, Paperless documents and the PDF archive; personal/media libraries excluded" \
  "Duration" "${elapsed}s" || echo "Backup success notification was not delivered" >&2
