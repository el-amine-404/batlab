#!/usr/bin/env bash
# Install the version-controlled Apprise webhook body in Uptime Kuma.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly CONTAINER="${UPTIME_KUMA_CONTAINER:-uptime-kuma}"
readonly TEMPLATE="$SCRIPT_DIR/notifications/apprise-body.liquid"
readonly REMOTE_TEMPLATE=/tmp/batlab-apprise-body.liquid

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
[[ -s "$TEMPLATE" ]] || { echo "missing template: $TEMPLATE" >&2; exit 1; }
docker inspect "$CONTAINER" >/dev/null

backup="/app/data/kuma.db.template-backup-$(date -u +%Y%m%dT%H%M%SZ)"
docker cp "$TEMPLATE" "$CONTAINER:$REMOTE_TEMPLATE"
docker exec "$CONTAINER" sqlite3 /app/data/kuma.db ".backup '$backup'"

changed="$(docker exec "$CONTAINER" sqlite3 /app/data/kuma.db \
  "BEGIN IMMEDIATE;
   UPDATE notification
      SET config = json_set(config, '$.webhookCustomBody',
                            CAST(readfile('$REMOTE_TEMPLATE') AS TEXT))
    WHERE name = 'Apprise (alerts)' AND json_extract(config, '$.type') = 'webhook';
   SELECT changes();
   COMMIT;")"
docker exec "$CONTAINER" rm -f "$REMOTE_TEMPLATE"

[[ "$changed" == 1 ]] || {
  echo "expected one Apprise (alerts) notification, updated: $changed" >&2
  exit 1
}

docker restart "$CONTAINER" >/dev/null
for _ in $(seq 1 60); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null)" == healthy ]] && {
    echo "Uptime Kuma notification template installed; database backup: $backup"
    exit 0
  }
  sleep 2
done
echo "Uptime Kuma did not become healthy after its notification template update" >&2
exit 1
