#!/usr/bin/env bash

set -Eeuo pipefail

readonly DUMP_DIR="${BATLAB_DUMP_DIR:-/mnt/docker-volumes/database-dumps}"
readonly PAPERLESS_DB_CONTAINER="${PAPERLESS_DB_CONTAINER:-paperless-db}"
readonly PAPERLESS_DUMP="$DUMP_DIR/paperless.dump"
readonly PAPERLESS_TMP="$PAPERLESS_DUMP.tmp"
readonly LOCK_FILE="$DUMP_DIR/.dump-databases.lock"

for command_name in docker flock; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command not found: $command_name" >&2
    exit 1
  }
done

install -d -m 700 "$DUMP_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another database export is already running" >&2
  exit 1
fi

rm -f -- "$PAPERLESS_TMP"
trap 'rm -f -- "$PAPERLESS_TMP"' EXIT

# A stopped Postgres leaves consistent files in its volume, which the snapshot
# already includes. The dump only matters while the database is live, and a
# stack stopped to save RAM must not abort the whole backup.
if [[ "$(docker inspect --format '{{.State.Running}}' "$PAPERLESS_DB_CONTAINER" 2>/dev/null || true)" != true ]]; then
  echo "Skipping Paperless export: $PAPERLESS_DB_CONTAINER is not running, its volume is backed up as-is" >&2
  if [[ -e "$PAPERLESS_DUMP" ]]; then
    echo "The previous $PAPERLESS_DUMP from $(date -r "$PAPERLESS_DUMP" '+%F %R') stays in the snapshot" >&2
  fi
  exit 0
fi

docker exec "$PAPERLESS_DB_CONTAINER" sh -ceu '
  command -v pg_dump >/dev/null
  command -v pg_restore >/dev/null
  test -n "$POSTGRES_USER"
  test -n "$POSTGRES_DB"
  pg_isready --quiet --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"
'

echo "Exporting Paperless PostgreSQL database..."
umask 077
docker exec "$PAPERLESS_DB_CONTAINER" sh -ceu \
  'exec pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
    --format=custom --no-owner --no-acl' \
  >"$PAPERLESS_TMP"

if [[ ! -s "$PAPERLESS_TMP" ]]; then
  echo "Paperless database export is empty" >&2
  exit 1
fi

# Parsing the table of contents catches a truncated or invalid custom archive.
if ! docker exec -i "$PAPERLESS_DB_CONTAINER" \
  pg_restore --list <"$PAPERLESS_TMP" >/dev/null; then
  echo "Paperless database export failed archive validation" >&2
  exit 1
fi

mv -f -- "$PAPERLESS_TMP" "$PAPERLESS_DUMP"
trap - EXIT
echo "Created $PAPERLESS_DUMP ($(du -h "$PAPERLESS_DUMP" | cut -f1))"
