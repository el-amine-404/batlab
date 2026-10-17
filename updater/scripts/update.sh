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

# What a locally built image was last built from (build_fingerprint), per
# service: the image name stays the same when only a pin or the code changes.
built_from() {
  cat "$STATE_DIR/built/$1" 2>/dev/null || true
}

record_build() {
  [[ "$DRY_RUN" == "1" ]] && return 0
  install -d -m 700 "$STATE_DIR/built"
  echo "$2" >"$STATE_DIR/built/$1"
}

sync_repository() {
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Working tree has local changes; not pulling." >&2
    notify alerts "Updater skipped: repository not clean" failure \
      "Fix" "cd ~/batlab && git status"
    exit 1
  fi
  git fetch --quiet origin "$BRANCH"
  if ! git merge --ff-only --quiet "origin/$BRANCH"; then
    notify alerts "Updater skipped: cannot fast-forward" failure \
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
  local stack="$1" service="$2" container="$3" old_image="$4" new_image="$5" built="$6" key="$7"
  local compose_file="compose/$stack/docker-compose.yml" reason override old_id new_id
  local fetch=(pull --quiet) start=(up -d --no-deps) previous="$old_image" keep="$old_image"
  # A locally built image is rebuilt from the new pins and code; there is
  # nothing to pull. Not --quiet: with it, Compose refuses the build's
  # network.host entitlement ("additional privileges requested"). Started
  # with --no-build (pull_policy: build would build it a second time) and
  # recreated whatever Compose thinks: the image name is often the same.
  [[ "$built" == "yes" ]] && fetch=(build) start=(up -d --no-deps --no-build --force-recreate)

  echo "Updating $service: $old_image -> $new_image"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  old_id="$(docker inspect -f '{{.Image}}' "$container")"
  if [[ "$built" == "yes" ]]; then
    # The rebuild may take the old image's name: the old build is kept under
    # another one, to roll back to.
    keep="local/batlab-rollback:$service"
    docker tag "$old_id" "$keep" 2>/dev/null || keep="$old_image"
    previous="$old_image (${old_id:7:12})"
  fi

  if docker compose "${ENV_ARGS[@]}" -f "$compose_file" "${fetch[@]}" "$service"; then
    new_id="$(docker image inspect -f '{{.Id}}' "$new_image" 2>/dev/null || true)"
    if docker compose "${ENV_ARGS[@]}" -f "$compose_file" "${start[@]}" "$service" &&
      reason="$(wait_until_healthy "$container")"; then
      notify updates "Updated $service" success \
        "Image" "$new_image${new_id:+ (${new_id:7:12})}" "Previous" "$previous"
      # The old image goes once nothing runs it (Docker's containerd store
      # would delete it from under a running container).
      if [[ "$(docker inspect -f '{{.Image}}' "$container")" != "$old_id" ]]; then
        [[ "$keep" == "$new_image" ]] || docker image rm "$keep" >/dev/null 2>&1 || true
        [[ "$built" == "yes" ]] && docker image rm "$old_id" >/dev/null 2>&1 || true
      fi
      return 0
    fi
  fi

  reason="${reason:-pull or recreate failed}"
  echo "Update of $service failed ($reason); rolling back to $old_image" >&2
  override="$(mktemp --suffix=.yml)"
  # pull_policy: the old image is still here; a built service would otherwise
  # be rebuilt from the new pins under the old name.
  printf 'services:\n  %s:\n    image: %s\n    pull_policy: missing\n' "$service" "$keep" >"$override"
  local rollback="rolled back"
  docker compose "${ENV_ARGS[@]}" -f "$compose_file" -f "$override" up -d --no-deps "$service" &&
    wait_until_healthy "$container" >/dev/null || rollback="ROLLBACK FAILED, check it now"
  rm -f "$override"

  [[ "$built" == "yes" ]] && docker tag "$keep" "$old_image" >/dev/null 2>&1 || true
  remember "$key" failed
  notify alerts "Update failed: $service" failure \
    "Tried" "$new_image" "Reason" "$reason" "Result" "$rollback to $previous" \
    "Logs" "docker logs --tail 50 $container"
  return 1
}

# Each service of a `docker compose config --format json` on stdin, one line
# each: name, container, image, built (yes/no) and, for a built one, the
# fingerprint of what it is built from: its build settings (the pins arrive as
# build args) and the git trees of its contexts, so a new pin or new code
# changes it. Run from the repository's root.
readonly BUILD_FINGERPRINT='
import hashlib, json, os, subprocess, sys

def tree(path):
    rel = os.path.relpath(path)
    found = subprocess.run(["git", "rev-parse", "HEAD:" + ("" if rel == "." else rel)],
                           capture_output=True, text=True)
    return found.stdout.strip() or "untracked:" + rel

for name, svc in json.load(sys.stdin)["services"].items():
    build, fingerprint = svc.get("build"), ""
    if build:
        contexts = {"": build.get("context", ".")} | (build.get("additional_contexts") or {})
        made_of = {k: v for k, v in build.items() if k not in ("context", "additional_contexts")}
        made_of["trees"] = {k: tree(v) for k, v in sorted(contexts.items())}
        fingerprint = hashlib.sha256(json.dumps(made_of, sort_keys=True).encode()).hexdigest()[:16]
    print(name, svc.get("container_name", name), svc.get("image", ""),
          "yes" if build else "no", fingerprint, sep="\t")
'

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
    while IFS=$'\t' read -r service container desired built fingerprint; do
      local running_image state recorded key
      state="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo missing)"
      [[ "$state" == "running" ]] || continue
      running_image="$(docker inspect -f '{{.Config.Image}}' "$container")"
      checked=$((checked + 1))
      key="$service $desired"
      if [[ "$built" == "yes" ]]; then
        # Built here: the same image name may hide new pins (a base image's
        # digest, which brings Debian's newest ffmpeg) or new code.
        key="$service $desired $fingerprint"
        recorded="$(built_from "$service")"
        if [[ "$running_image" == "$desired" ]]; then
          # First sight: what runs was built from this checkout (make up).
          [[ -n "$recorded" ]] || { record_build "$service" "$fingerprint"; continue; }
          [[ "$recorded" == "$fingerprint" ]] && continue
        fi
      elif [[ "$running_image" == "$desired" ]]; then
        continue
      fi
      changed=$((changed + 1))

      if printf '%s\n' "${allowed[@]}" | grep -qxF "$service"; then
        if seen "$key" failed; then
          echo "Skipping $service: $desired already failed once"
          continue
        fi
        if update_service "$stack" "$service" "$container" "$running_image" "$desired" "$built" "$key"; then
          [[ "$built" == "yes" ]] && record_build "$service" "$fingerprint"
        else
          failures=$((failures + 1))
        fi
      elif ! seen "$key" pending; then
        echo "Manual update pending for $service: $running_image -> $desired"
        remember "$key" pending
        notify updates "Manual update waiting: $service" info \
          "Running" "$running_image" "In git" "$desired${fingerprint:+ (new pins or code)}" \
          "Apply" "make up STACK=$stack SERVICE=$service"
      fi
    done < <(docker compose "${ENV_ARGS[@]}" -f "$stack_file" config --format json 2>/dev/null |
      python3 -c "$BUILD_FINGERPRINT")
  done

  echo "Checked $checked running service(s): $changed differ from the checkout, $failures failed."
  ((failures == 0))
}

# Sourcing the script exposes the functions without running an update.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
