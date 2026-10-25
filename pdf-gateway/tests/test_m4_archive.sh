#!/usr/bin/env bash
# M4 slice 1: portable archive library and CLI on a real filesystem. Runs on any
# Docker host (use the local PC, not Lab2): networkless, read-only image, 1 CPU.
set -Eeuo pipefail
trap 'echo "test_m4_archive.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
scratch="$(mktemp -d)"
image=pdf-gateway-m4-test/archive-worker:local
cleanup() { status=$?; rm -rf -- "$scratch"; exit "$status"; }
trap cleanup EXIT

docker build -q -f "$root/pdf-gateway/archive-worker/Dockerfile" -t "$image" "$root/pdf-gateway" >/dev/null
docker run --rm --network none --read-only --tmpfs /tmp:size=16m --cap-drop ALL --security-opt no-new-privileges \
  --cpus 1 --memory 512m --pids-limit 64 --user "$(id -u):$(id -g)" \
  -v "$scratch:/data" -v "$root/pdf-gateway/tests/archive_test.py:/tests/archive_test.py:ro" \
  --entrypoint python "$image" /tests/archive_test.py /data
