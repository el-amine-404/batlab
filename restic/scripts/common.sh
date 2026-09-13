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
