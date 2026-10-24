#!/usr/bin/env bash

set -Eeuo pipefail

readonly DUMP_DIR="${BATLAB_DUMP_DIR:-/mnt/docker-volumes/database-dumps}"
readonly LOCK_FILE="$DUMP_DIR/.dump-databases.lock"

for command_name in docker flock; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command not found: $command_name" >&2
    exit 1
  }
done

install -d -m 700 "$DUMP_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another database export is already running" >&2; exit 1; }

temporary=()
cleanup() { rm -f -- "${temporary[@]}"; }
trap cleanup EXIT

dump_postgres() {
  local label="$1" container="$2" destination="$3" temporary_dump="$3.tmp"
  temporary+=("$temporary_dump")
  rm -f -- "$temporary_dump"

  # A cleanly stopped PostgreSQL volume is consistent on disk. Preserve the
  # previous dump and let Restic also capture the raw volume in that case.
  if [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" != true ]]; then
    echo "Skipping $label export: $container is not running; its volume is backed up as-is" >&2
    [[ ! -e "$destination" ]] || echo "Previous dump from $(date -r "$destination" '+%F %R') remains" >&2
    return 0
  fi

  docker exec "$container" sh -ceu '
    command -v pg_dump >/dev/null
    command -v pg_restore >/dev/null
    test -n "$POSTGRES_USER"
    test -n "$POSTGRES_DB"
    pg_isready --quiet --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"
  '

  echo "Exporting $label PostgreSQL database..."
  umask 077
  docker exec "$container" sh -ceu \
    'exec pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl' \
    >"$temporary_dump"
  [[ -s "$temporary_dump" ]] || { echo "$label database export is empty" >&2; return 1; }
  docker exec -i "$container" pg_restore --list <"$temporary_dump" >/dev/null || {
    echo "$label database export failed archive validation" >&2
    return 1
  }
  mv -f -- "$temporary_dump" "$destination"
  echo "Created $destination ($(du -h "$destination" | cut -f1))"
}

dump_postgres Paperless "${PAPERLESS_DB_CONTAINER:-paperless-db}" "$DUMP_DIR/paperless.dump"
dump_postgres Immich "${IMMICH_DB_CONTAINER:-immich-db}" "$DUMP_DIR/immich.dump"
