#!/usr/bin/env bash
# Security and load checks of batlab's own apps, on lab2, against throwaway
# copies (README.md). Each copy runs the image the live app runs, on a copy of
# its data, on an internal Docker network: no route out at all, so nothing
# reaches YouTube, another site or the home network, whatever the checks send.
#
#   appcheck/run.sh [subfeed videodrop clipseerr songdrop]   (default: all four)
#   appcheck/run.sh static                                    bandit, pip-audit, npm audit
#   APPCHECK_IMAGE_SUBFEED=<image> appcheck/run.sh subfeed    another image than the live one
#   APPCHECK_ENV="-e SUBFEED_THREADS=8" APPCHECK_ONLY=load …   another setting, the load only
#
# Results: appcheck/out/<date>/<app>/ (load.json, schemathesis.txt, stats.txt, app.log).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$HERE")
VOLUMES=${VOLUMES_ROOT:-/mnt/docker-volumes}
NET=appcheck-net
TOOLS=local/batlab-appcheck
OUT=${APPCHECK_OUT:-$HERE/out/$(date +%F)}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# app: port, state folder (under VOLUMES), permissions for the key, extra docker run flags, live stream
declare -A PORT=([subfeed]=8830 [videodrop]=8840 [clipseerr]=8820 [songdrop]=8810)
declare -A STATE=([subfeed]=subfeed [videodrop]=videodrop [clipseerr]=clipseerr [songdrop]=songdrop/jobs)
declare -A CAN=([subfeed]=feeds,settings [videodrop]=download,destinations,everyone,settings
                [clipseerr]=request,everyone,settings [songdrop]=download,everyone,settings)
declare -A EXTRA=([subfeed]="-e SUBFEED_FEEDS=" [videodrop]="-v MEDIA:/media" [clipseerr]="" [songdrop]="-e SONGDROP_PORT=8810")
declare -A SSE=([videodrop]=/api/v1/jobs/events)

# Host network: lab2's default bridge has no DNS, and pip needs PyPI.
tools() { docker build -q --network host -t "$TOOLS" "$HERE" > /dev/null; }

static() {
  mkdir -p "$OUT/static"
  # Host network: pip-audit asks PyPI's advisory database, npm audit npm's.
  for dir in compose/subfeed/app compose/videodrop/app compose/clipseerr/app compose/songdrop/app compose/musicguard lib/batlab-common; do
    name=$(echo "$dir" | tr / _)
    docker run --rm --network host -v "$REPO:/repo:ro" "$TOOLS" \
      sh -c "cd /repo/$dir && bandit -q -r . -x ./tests,./backend/tests,./frontend,./docs -f txt" > "$OUT/static/bandit_$name.txt" 2>&1 || true
    for req in "$REPO/$dir/backend/requirements.txt" "$REPO/$dir/requirements.txt"; do
      [ -f "$req" ] || continue
      docker run --rm --network host -v "$req:/req.txt:ro" "$TOOLS" \
        pip-audit --no-deps --disable-pip -r /req.txt > "$OUT/static/pip-audit_$name.txt" 2>&1 || true
    done
    if [ -f "$REPO/$dir/frontend/package-lock.json" ]; then
      docker run --rm --network host -v "$REPO/$dir/frontend:/f:ro" -w /f node:24.22.0-alpine3.24 \
        npm audit --omit=dev --audit-level=low > "$OUT/static/npm-audit_$name.txt" 2>&1 || true
    fi
  done
  echo "static: $OUT/static"
}

check() {
  local app=$1 port=${PORT[$1]} name=appcheck-$1 dir="$OUT/$1"
  local image limit pids
  # The live app's image, or another to compare (APPCHECK_IMAGE_SUBFEED=…).
  local override="APPCHECK_IMAGE_${app^^}"
  image=${!override:-$(docker inspect -f '{{.Config.Image}}' "$app")}
  limit=$(docker inspect -f '{{.HostConfig.Memory}}' "$app")
  pids=$(docker inspect -f '{{.HostConfig.PidsLimit}}' "$app")
  mkdir -p "$dir" "$WORK/$app/state" "$WORK/$app/media/music-videos"
  cp -a "$VOLUMES/${STATE[$app]}/." "$WORK/$app/state/"
  local extra=${EXTRA[$app]//MEDIA/$WORK/$app/media}
  # More flags for the copy, to compare settings (APPCHECK_ENV="-e SUBFEED_THREADS=8").
  extra="$extra ${APPCHECK_ENV:-}"

  docker rm -f "$name" > /dev/null 2>&1 || true
  # shellcheck disable=SC2086 # extra is a list of flags
  docker run -d --name "$name" --network "$NET" --user 1000:1000 --memory "$limit" --pids-limit "$pids" \
    -v "$WORK/$app/state:/state" $extra "$image" > /dev/null
  for _ in $(seq 60); do
    docker run --rm --network "$NET" "$TOOLS" python -c \
      "import httpx; httpx.get('http://$name:$port/api/v1/health', timeout=2).raise_for_status()" 2> /dev/null && break
    sleep 2
  done
  local key
  key=$(docker exec "$name" admin add-key appcheck --can "${CAN[$app]}" | grep -oE '[A-Za-z0-9_-]{24,}' | tail -1)

  # Memory and CPU every 2 s while the checks run.
  ( while docker inspect "$name" > /dev/null 2>&1; do
      docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}} {{.PIDs}}' "$name" 2> /dev/null || true
      sleep 2
    done ) > "$dir/stats.txt" &
  local sampler=$!

  echo "== $app ($image)"
  docker run --rm --network "$NET" -v "$dir:/out" "$TOOLS" python load.py --base "http://$name:$port" --key "$key" \
    ${SSE[$app]:+--sse ${SSE[$app]}} ${APPCHECK_LOAD_ARGS:-} --out /out/load.json | tee "$dir/load.txt"
  [ "${APPCHECK_ONLY:-}" = load ] || docker run --rm --network "$NET" "$TOOLS" schemathesis run "http://$name:$port/api/v1/openapi.json" \
    --url "http://$name:$port" -H "X-Api-Key: $key" --checks all --max-examples 30 --workers 4 --request-timeout 30 \
    > "$dir/schemathesis.txt" 2>&1 || true
  [ "${APPCHECK_ONLY:-}" = load ] || tail -n 25 "$dir/schemathesis.txt"

  docker logs "$name" > "$dir/app.log" 2>&1
  docker inspect -f 'oom_killed={{.State.OOMKilled}} restarts={{.RestartCount}} status={{.State.Status}}' "$name" > "$dir/end.txt"
  docker rm -f "$name" > /dev/null
  kill "$sampler" 2> /dev/null || true
}

docker network inspect "$NET" > /dev/null 2>&1 || docker network create --internal "$NET" > /dev/null
tools
if [ "${1:-}" = static ]; then static; exit; fi
for app in "${@:-subfeed videodrop clipseerr songdrop}"; do
  # shellcheck disable=SC2086
  for a in $app; do check "$a"; done
done
echo "results: $OUT"
