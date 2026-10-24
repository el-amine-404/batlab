#!/usr/bin/env bash
# Restore a small recovery set from the newest off-site snapshot and validate it.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_restic_config
require_command restic
require_command docker

started="$(date +%s)"
stage="initialization"
work="$(mktemp -d /var/tmp/batlab-restic-restore.XXXXXX)"
finish() {
  local status="$1" elapsed="$(($(date +%s) - started))"
  if [[ "$work" == /var/tmp/batlab-restic-restore.* && -d "$work" ]]; then
    rm -rf --one-file-system -- "$work"
  fi
  if ((status)); then
    restic_notify failure "Backup restore test failed" \
      "Issue" "$stage failed with exit status $status" \
      "Impact" "The newest backup has not proved recoverable" \
      "Fix" "Inspect: journalctl -u batlab-restic-restore-test -n 100; then run: sudo restic/scripts/restore-test.sh" \
      "Duration" "${elapsed}s" || echo "Restore-test failure notification was not delivered" >&2
  fi
}
trap 'finish "$?"' EXIT

stage="restore from newest snapshot"
restic restore latest --target "$work" \
  --include /mnt/docker-volumes/database-dumps/paperless.dump \
  --include /mnt/docker-volumes/database-dumps/immich.dump \
  --include /mnt/docker-volumes/database-dumps/sqlite/netalertx/db/app.db \
  --include /home/potato/batlab/compose/.env \
  --include /home/potato/batlab/compose/versions.env

stage="excluded Frigate media audit"
if restic ls latest | grep -Eq '^/mnt/docker-volumes/frigate/(media\.img|media/)'; then
  echo "The newest snapshot unexpectedly contains Frigate camera media" >&2
  exit 1
fi

paperless="$work/mnt/docker-volumes/database-dumps/paperless.dump"
immich="$work/mnt/docker-volumes/database-dumps/immich.dump"
sqlite="$work/mnt/docker-volumes/database-dumps/sqlite/netalertx/db/app.db"
versions="$work/home/potato/batlab/compose/versions.env"
secrets="$work/home/potato/batlab/compose/.env"
for restored in "$paperless" "$immich" "$sqlite" "$versions" "$secrets"; do
  [[ -s "$restored" ]] || { echo "Missing or empty restored file: $restored" >&2; exit 1; }
done

stage="secrets-file validation"
[[ "$(stat -c %a "$secrets")" == 600 ]] || {
  echo "Restored compose/.env does not have mode 600" >&2
  exit 1
}
grep -q '^DISCORD_WEBHOOK_BACKUPS=' "$secrets" || {
  echo "Restored compose/.env lacks DISCORD_WEBHOOK_BACKUPS" >&2
  exit 1
}
if grep -q '^DISCORD_WABHOOK_BACKUPS=' "$secrets"; then
  echo "Restored compose/.env still contains the misspelled backup webhook name" >&2
  exit 1
fi

stage="NetAlertX SQLite integrity check"
python3 - "$sqlite" <<'PY'
import sqlite3, sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as database:
    result = database.execute("PRAGMA quick_check").fetchone()[0]
if result != "ok":
    raise SystemExit(f"SQLite quick_check: {result}")
PY

stage="Paperless PostgreSQL archive validation"
docker exec -i paperless-db pg_restore --list <"$paperless" >/dev/null
stage="Immich PostgreSQL archive validation"
docker exec -i immich-db pg_restore --list <"$immich" >/dev/null

elapsed="$(($(date +%s) - started))"
stage="success notification"
restic_notify success "Backup restore test passed" \
  "Validated" "Paperless PostgreSQL, Immich PostgreSQL, NetAlertX SQLite, encrypted secrets, repository configuration and Frigate-media exclusion" \
  "Source" "Newest off-site Restic snapshot" \
  "Action" "None" \
  "Duration" "${elapsed}s" || echo "Restore-test success notification was not delivered" >&2
