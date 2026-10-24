#!/usr/bin/env bash

set -Eeuo pipefail

readonly RESTIC_CONFIG_FILE="${RESTIC_CONFIG_FILE:-/etc/batlab-restic/restic.env}"

load_restic_config() {
  if [[ ! -r "$RESTIC_CONFIG_FILE" ]]; then
    echo "Cannot read $RESTIC_CONFIG_FILE" >&2
    exit 1
  fi

  set -a
  # shellcheck source=/dev/null
  source "$RESTIC_CONFIG_FILE"
  set +a

  : "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is not configured}"
  : "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is not configured}"
  : "${RCLONE_CONFIG:?RCLONE_CONFIG is not configured}"

  if [[ ! -r "$RESTIC_PASSWORD_FILE" ]]; then
    echo "Cannot read Restic password file: $RESTIC_PASSWORD_FILE" >&2
    exit 1
  fi

  install -d -m 700 "${RESTIC_CACHE_DIR:-/var/cache/batlab-restic}"
}

healthcheck_ping() {
  [[ -n "${RESTIC_HEALTHCHECK_URL:-}" ]] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null "$RESTIC_HEALTHCHECK_URL/$1" || true
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 1
  }
}

backup_webhook() {
  local env_file="${BATLAB_COMPOSE_ENV:-/home/potato/batlab/compose/.env}"
  python3 - "$env_file" <<'PY'
import sys
try:
    lines = open(sys.argv[1], encoding="utf-8")
except OSError:
    raise SystemExit(0)
values = {}
for raw in lines:
    if "=" not in raw or raw.lstrip().startswith("#"):
        continue
    key, value = raw.rstrip("\n").split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")
print(values.get("DISCORD_WEBHOOK_BACKUPS") or values.get("DISCORD_WABHOOK_BACKUPS") or "")
PY
}

# restic_notify KIND TITLE FIELD VALUE...; notification failure never changes
# backup integrity, but is visible in the journal and Healthchecks still fires.
restic_notify() {
  local kind="$1" title="$2" fields=() fallback
  shift 2
  while (($#)); do
    fields+=(--field "$1" "${2-}")
    shift
    (($#)) && shift
  done
  fallback="$(backup_webhook)"
  BATLAB_NOTIFY_FALLBACK="$fallback" python3 -I \
    "$SCRIPT_DIR/../../lib/batlab-common/batlab_common/notify.py" \
    restic backups "$kind" "$title" "${fields[@]}"
}
