#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
readonly REPO_DIR
readonly STATE_DIR="${UPDATER_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/batlab-updater}"
readonly HEALTH_TIMEOUT="${UPDATER_HEALTH_TIMEOUT:-300}"
readonly NO_HEALTHCHECK_GRACE="${UPDATER_NO_HEALTHCHECK_GRACE:-60}"
readonly BRANCH="${UPDATER_BRANCH:-main}"
readonly DRY_RUN="${UPDATER_DRY_RUN:-0}"
readonly NOTIFY="${UPDATER_NOTIFY:-1}"

cd "$REPO_DIR"
install -d -m 700 "$STATE_DIR"

env_value() {
  sed -n "s/^$1=//p" compose/.env | tail -n 1
}

HOST_PROFILE_NAME="$(env_value HOST_PROFILE)"
WEBHOOK_OK="$(env_value DISCORD_WEBHOOK_UPDATES)"
WEBHOOK_FAIL="$(env_value DISCORD_WEBHOOK_ALERTS)"
readonly HOST_PROFILE_NAME WEBHOOK_OK WEBHOOK_FAIL
readonly ENV_ARGS=(--env-file compose/versions.env --env-file compose/.env --env-file "hosts/$HOST_PROFILE_NAME/compose.env")

# One message through Apprise as "updater on <host>" (lib/batlab-common,
# senders.json), straight to the channel's Discord webhook if Apprise is down,
# as it is while the updater restarts it. Best effort.
#   notify CHANNEL TITLE KIND [FIELD_NAME FIELD_VALUE]...
notify() {
  local channel="$1" title="$2" kind="$3"
  shift 3
  [[ "$DRY_RUN" != "1" && "$NOTIFY" == "1" ]] || { echo "notify: $title | $*"; return 0; }
  local fallback="$WEBHOOK_FAIL" fields=()
  [[ "$channel" == updates ]] && fallback="$WEBHOOK_OK"
  while (($#)); do
    fields+=(--field "$1" "${2-}")
    shift
    (($#)) && shift
  done
  BATLAB_NOTIFY_FALLBACK="$fallback" python3 -I "$REPO_DIR/lib/batlab-common/batlab_common/notify.py" \
    updater "$channel" "$kind" "$title" "${fields[@]}" || true
}


# Remembers which target images were already reported, so a failed or manual
# update is announced once rather than every night.
seen() {
  grep -qxF "$1" "$STATE_DIR/$2" 2>/dev/null
}

remember() {
  [[ "$DRY_RUN" == "1" ]] && return 0
  echo "$1" >>"$STATE_DIR/$2"
}

sync_repository() {
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Working tree has local changes; not pulling." >&2
    notify alerts "🔴 Updater skipped: repository not clean" failure \
      "Fix" "cd ~/batlab && git status"
    exit 1
  fi
  git fetch --quiet origin "$BRANCH"
  if ! git merge --ff-only --quiet "origin/$BRANCH"; then
    notify alerts "🔴 Updater skipped: cannot fast-forward" failure \
      "Fix" "cd ~/batlab && git status && git pull"
    exit 1
  fi
}

wait_until_healthy() {
  local container="$1" deadline=$((SECONDS + HEALTH_TIMEOUT)) has_health state health
  has_health="$(docker inspect -f '{{if .State.Health}}yes{{end}}' "$container" 2>/dev/null || true)"

  while ((SECONDS < deadline)); do
    state="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo missing)"
    [[ "$state" == "running" ]] || { echo "container is $state"; return 1; }

    if [[ "$has_health" == "yes" ]]; then
      health="$(docker inspect -f '{{.State.Health.Status}}' "$container")"
      [[ "$health" == "healthy" ]] && return 0
      [[ "$health" == "unhealthy" ]] && { echo "healthcheck reports unhealthy"; return 1; }
    elif ((SECONDS >= deadline - HEALTH_TIMEOUT + NO_HEALTHCHECK_GRACE)); then
      return 0
    fi
    sleep 5
  done
  echo "not healthy after ${HEALTH_TIMEOUT}s"
  return 1
}

update_service() {
  local stack="$1" service="$2" container="$3" old_image="$4" new_image="$5" built="$6"
  local compose_file="compose/$stack/docker-compose.yml" reason override
  local fetch=(pull --quiet)
  # A locally built image (music-links' songdrop) is rebuilt from the new pins;
  # there is nothing to pull. Not --quiet: with it, Compose refuses the
  # build's network.host entitlement ("additional privileges requested").
  [[ "$built" == "yes" ]] && fetch=(build)

  echo "Updating $service: $old_image -> $new_image"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  if docker compose "${ENV_ARGS[@]}" -f "$compose_file" "${fetch[@]}" "$service" &&
    docker compose "${ENV_ARGS[@]}" -f "$compose_file" up -d --no-deps "$service" &&
    reason="$(wait_until_healthy "$container")"; then
    notify updates "🟢 Updated $service" success \
      "Image" "$new_image" "Previous" "$old_image"
    docker image rm "$old_image" >/dev/null 2>&1 || true
    return 0
  fi

  reason="${reason:-pull or recreate failed}"
  echo "Update of $service failed ($reason); rolling back to $old_image" >&2
  override="$(mktemp --suffix=.yml)"
  # pull_policy: the old image is still here; a built service would otherwise
  # be rebuilt from the new pins under the old name.
  printf 'services:\n  %s:\n    image: %s\n    pull_policy: missing\n' "$service" "$old_image" >"$override"
  local rollback="rolled back"
  docker compose "${ENV_ARGS[@]}" -f "$compose_file" -f "$override" up -d --no-deps "$service" &&
    wait_until_healthy "$container" >/dev/null || rollback="ROLLBACK FAILED, check it now"
  rm -f "$override"

  remember "$service $new_image" failed
  notify alerts "🔴 Update failed: $service" failure \
    "Tried" "$new_image" "Reason" "$reason" "Result" "$rollback to $old_image" \
    "Logs" "docker logs --tail 50 $container"
  return 1
}

main() {
  exec 9>"$STATE_DIR/lock"
  flock -n 9 || { echo "Another updater run is in progress."; exit 0; }

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "Dry run: not pulling, only comparing running images with the checkout."
  else
    sync_repository
  fi

  mapfile -t allowed < <(grep -vE '^\s*(#|$)' "$SCRIPT_DIR/../conf/auto-services.txt")
  local failures=0 checked=0 changed=0 stack_file stack

  # The host's stacks only, as `make up` runs them: another host's stack may not
  # even render here (adguardhome-sync needs lab1's ADGUARD_ORIGIN_IP), and its
  # failure printed a traceback every night.
  local host_stacks="hosts/$HOST_PROFILE_NAME/stacks.txt"
  for stack_file in compose/*/docker-compose.yml; do
    stack="$(basename "$(dirname "$stack_file")")"
    if [[ -f "$host_stacks" ]] && ! grep -vE '^\s*(#|$)' "$host_stacks" | grep -qxF "$stack"; then
      continue
    fi
    while IFS=$'\t' read -r service container desired built; do
      local running_image state
      state="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo missing)"
      [[ "$state" == "running" ]] || continue
      running_image="$(docker inspect -f '{{.Config.Image}}' "$container")"
      checked=$((checked + 1))
      [[ "$running_image" == "$desired" ]] && continue
      changed=$((changed + 1))

      if printf '%s\n' "${allowed[@]}" | grep -qxF "$service"; then
        if seen "$service $desired" failed; then
          echo "Skipping $service: $desired already failed once"
          continue
        fi
        update_service "$stack" "$service" "$container" "$running_image" "$desired" "$built" || failures=$((failures + 1))
      elif ! seen "$service $desired" pending; then
        echo "Manual update pending for $service: $running_image -> $desired"
        remember "$service $desired" pending
        notify updates "⚪ Manual update waiting: $service" info \
          "Running" "$running_image" "In git" "$desired" \
          "Apply" "make up STACK=$stack SERVICE=$service"
      fi
    done < <(docker compose "${ENV_ARGS[@]}" -f "$stack_file" config --format json 2>/dev/null |
      python3 -c '
import json, sys
for name, svc in json.load(sys.stdin)["services"].items():
    print(name, svc.get("container_name", name), svc.get("image", ""),
          "yes" if "build" in svc else "no", sep="\t")
')
  done

  echo "Checked $checked running service(s): $changed differ from the checkout, $failures failed."
  ((failures == 0))
}

# Sourcing the script exposes the functions without running an update.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
