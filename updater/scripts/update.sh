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
PULLED_FROM=""

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
  PULLED_FROM="$(git rev-parse HEAD)"
  if ! git merge --ff-only --quiet "origin/$BRANCH"; then
    notify alerts "Updater skipped: cannot fast-forward" failure \
      "Fix" "cd ~/batlab && git status && git pull"
    exit 1
  fi
}

# What this run's pull brought to a stack: Renovate's and your commit titles, and
# the pins it builds from or runs. For the #updates card.
changes_since_pull() {
  local stack="$1" pin value
  [[ -n "$PULLED_FROM" && "$PULLED_FROM" != "$(git rev-parse HEAD)" ]] || return 0
  {
    git log --format=%s "$PULLED_FROM..HEAD" -- "compose/$stack" lib/batlab-common lib/batlab-ui
    git diff -U0 "$PULLED_FROM" HEAD -- compose/versions.env |
      sed -nE 's/^\+((TAG|VERSION)_[A-Z0-9_]+)=(.*)$/\1 \3/p' |
      while read -r pin value; do
        grep -qF "\${$pin" "compose/$stack/docker-compose.yml" && echo "$pin ${value%%@*}"
      done
  } | head -8
}

# A file bind-mounted from the checkout keeps the inode it had when its
# container started, and git replaces a file it changes: the container goes on
# reading the old copy until it is recreated. Netdata sent its old cards for
# days that way (2026-10-20). Restarts each running container with a bind
# mount from the checkout that the pull changed, file or directory.
restart_changed_mounts() {
  local from="$1" repo container source file
  repo="$(readlink -f "$REPO_DIR")"
  [[ -n "$from" && "$from" != "$(git rev-parse HEAD)" ]] || return 0
  mapfile -t changed < <(git diff --name-only "$from" HEAD)
  ((${#changed[@]})) || return 0

  for container in $(docker ps --format '{{.Names}}'); do
    local hits=()
    while read -r source; do
      [[ -n "$source" ]] || continue
      source="$(readlink -f "$source" 2>/dev/null)" || continue
      [[ "$source" == "$repo"/* ]] || continue
      for file in "${changed[@]}"; do
        [[ "$repo/$file" == "$source" || "$repo/$file" == "$source"/* ]] && hits+=("$file")
      done
    done < <(docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{println .Source}}{{end}}{{end}}' "$container")
    ((${#hits[@]})) || continue

    echo "Restarting $container: ${hits[*]} changed"
    [[ "$DRY_RUN" == "1" ]] && continue
    if docker restart "$container" >/dev/null && wait_until_healthy "$container" >/dev/null; then
      notify updates "Restarted $container for its changed configuration" success \
        "Changed" "$(printf '%s\n' "${hits[@]}" | sort -u | head -5)"
    else
      notify alerts "Restart failed: $container" failure \
        "Changed" "$(printf '%s\n' "${hits[@]}" | sort -u | head -5)" \
        "Logs" "docker logs --tail 50 $container"
    fi
  done
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

cool_enough() {
  while [[ "$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)" -gt 75000 ]]; do
    echo "CPU above 75 C, waiting..."
    sleep 20
  done
}

# Built as make builds: Compose's own build refuses network: host since it went
# through bake ("additional privileges requested", and it has no --allow), which
# failed every local app's rebuild each night until 2026-11-01.
build_local() {
  local compose_file="$1" service="$2" argv
  cool_enough
  mapfile -d '' -t argv < <(docker compose "${ENV_ARGS[@]}" -f "$compose_file" config --format json | python3 -c '
import json, os, sys
svc = json.load(sys.stdin)["services"][sys.argv[1]]
build = svc["build"]
argv = ["--network", "host", "-t", svc["image"], "-f", os.path.join(build["context"], build.get("dockerfile", "Dockerfile"))]
for key, value in (build.get("args") or {}).items():
    argv += ["--build-arg", f"{key}={value}"]
for key, value in (build.get("additional_contexts") or {}).items():
    argv += ["--build-context", f"{key}={value}"]
if build.get("target"):
    argv += ["--target", build["target"]]
sys.stdout.write("\0".join([*argv, build["context"]]) + "\0")' "$service")
  ((${#argv[@]})) && docker build "${argv[@]}"
}

# The PDF gateway is one unit: images shared between services, a sandbox image in
# the provider's podman, the provider itself on the host, and a temperature-gated
# build (make). It parses hostile files, so it always follows its pins and code.
gateway_fingerprint() {
  local path
  for path in compose/pdf-gateway lib/batlab-ui lib/batlab-common hosts/lab2/pdf-cdr-provider; do
    git rev-parse "HEAD:$path"
  done | sha256sum | cut -c1-16
}

gateway_healthy() {
  local container
  for container in $(docker ps -a --filter name=^pdf-gateway- --format '{{.Names}}'); do
    wait_until_healthy "$container" >/dev/null || { echo "$container"; return 1; }
  done
}

update_pdf_gateway() {
  grep -vE '^\s*(#|$)' "hosts/$HOST_PROFILE_NAME/stacks.txt" 2>/dev/null | grep -qxF pdf-gateway || return 0
  local fingerprint recorded image reason images=() provider_old
  fingerprint="$(gateway_fingerprint)"
  recorded="$(built_from pdf-gateway)"
  [[ -n "$recorded" ]] || { record_build pdf-gateway "$fingerprint"; return 0; }
  [[ "$recorded" == "$fingerprint" ]] && return 0
  if seen "pdf-gateway $fingerprint" failed; then
    echo "Skipping pdf-gateway: $fingerprint already failed once"
    return 0
  fi
  echo "Updating pdf-gateway: $recorded -> $fingerprint"
  [[ "$DRY_RUN" == "1" ]] && return 0

  mapfile -t images < <(docker ps --filter name=^pdf-gateway- --format '{{.Image}}' | grep '^local/' | sort -u)
  for image in "${images[@]}"; do
    docker tag "$image" "local/batlab-rollback:${image#local/}" || true
  done
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  provider_old="$(cat "$HOME/.local/lib/pdf-cdr-provider/provider.py" "$HOME/.local/lib/pdf-cdr-provider/shape_writer.py" 2>/dev/null | sha256sum)"
  if hosts/lab2/pdf-cdr-provider/install.sh >/dev/null &&
    { [[ "$provider_old" == "$(cat "$HOME/.local/lib/pdf-cdr-provider/provider.py" "$HOME/.local/lib/pdf-cdr-provider/shape_writer.py" | sha256sum)" ]] ||
      systemctl --user restart pdf-cdr-provider; } &&
    make up STACK=pdf-gateway && reason="$(gateway_healthy)"; then
    record_build pdf-gateway "$fingerprint"
    notify updates "Updated pdf-gateway" success "Built from" "$fingerprint" "Previous" "$recorded"
    return 0
  fi

  echo "Update of pdf-gateway failed (${reason:-build or start}); rolling back" >&2
  for image in "${images[@]}"; do
    docker tag "local/batlab-rollback:${image#local/}" "$image" || true
  done
  local rollback="rolled back"
  docker compose "${ENV_ARGS[@]}" -f compose/pdf-gateway/docker-compose.yml up -d --no-build --force-recreate &&
    gateway_healthy >/dev/null || rollback="ROLLBACK FAILED, check it now"
  remember "pdf-gateway $fingerprint" failed
  notify alerts "Update failed: pdf-gateway" failure "Tried" "$fingerprint" "Reason" "${reason:-build or start failed}" \
    "Result" "$rollback" "Logs" "journalctl -u batlab-updater -n 100"
  return 1
}

# Scanloom is one unit too: api, worker and notifier share an image that only api
# builds, and its name carries the version, so the services must move together.
scanloom_fingerprint() {
  {
    git rev-parse HEAD:compose/scanloom HEAD:lib/batlab-ui HEAD:lib/batlab-common
    grep -E '^(IMAGE|TAG)_(SCANLOOM_|CADDY)' compose/versions.env
  } | sha256sum | cut -c1-16
}

scanloom_healthy() {
  local container
  for container in $(docker ps -a --filter label=com.docker.compose.project=scanloom --format '{{.Names}}'); do
    wait_until_healthy "$container" >/dev/null || { echo "$container"; return 1; }
  done
}

update_scanloom() {
  grep -vE '^\s*(#|$)' "hosts/$HOST_PROFILE_NAME/stacks.txt" 2>/dev/null | grep -qxF scanloom || return 0
  local fingerprint recorded reason="" changes override container service image
  fingerprint="$(scanloom_fingerprint)"
  recorded="$(built_from scanloom)"
  [[ -n "$recorded" ]] || { record_build scanloom "$fingerprint"; return 0; }
  [[ "$recorded" == "$fingerprint" ]] && return 0
  if seen "scanloom $fingerprint" failed; then
    echo "Skipping scanloom: $fingerprint already failed once"
    return 0
  fi
  echo "Updating scanloom: $recorded -> $fingerprint"
  [[ "$DRY_RUN" == "1" ]] && return 0

  # The new version has a new image name, so the rollback names each service's
  # old image rather than retagging it.
  override="$(mktemp --suffix=.yml)"
  echo 'services:' >"$override"
  for container in $(docker ps --filter label=com.docker.compose.project=scanloom --format '{{.Names}}'); do
    image="$(docker inspect -f '{{.Config.Image}}' "$container")"
    [[ "$image" == local/* ]] || continue
    service="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$container")"
    docker tag "$(docker inspect -f '{{.Image}}' "$container")" "local/batlab-rollback:scanloom-$service"
    printf '  %s:\n    image: %s\n    pull_policy: missing\n' "$service" "local/batlab-rollback:scanloom-$service" >>"$override"
  done

  changes="$(changes_since_pull scanloom)"
  if make up STACK=scanloom && reason="$(scanloom_healthy)"; then
    rm -f "$override"
    record_build scanloom "$fingerprint"
    notify updates "Updated scanloom" success "Changes" "${changes:-$fingerprint}" "Previous" "$recorded"
    return 0
  fi

  echo "Update of scanloom failed (${reason:-build or start}); rolling back" >&2
  local rollback="rolled back"
  docker compose "${ENV_ARGS[@]}" -f compose/scanloom/docker-compose.yml -f "$override" up -d --no-build &&
    scanloom_healthy >/dev/null || rollback="ROLLBACK FAILED, check it now"
  rm -f "$override"
  remember "scanloom $fingerprint" failed
  notify alerts "Update failed: scanloom" failure "Tried" "${changes:-$fingerprint}" \
    "Reason" "${reason:-build or start failed}" "Result" "$rollback" \
    "Logs" "journalctl -u batlab-updater -n 100"
  return 1
}

# A Renovate branch whose build or tests failed never reaches main, so nothing
# else would say that dependency is stuck. Once per commit of the branch.
report_stuck_updates() {
  local branch sha link
  while IFS=$'\t' read -r branch sha link; do
    seen "stuck $branch $sha" pending && continue
    remember "stuck $branch $sha" pending
    notify updates "Update not merged: $branch" info \
      "Why" "Its build or tests failed" "Details" "$link"
  done < <(python3 -I "$SCRIPT_DIR/stuck_updates.py")
}

update_service() {
  local stack="$1" service="$2" container="$3" old_image="$4" new_image="$5" built="$6" key="$7"
  local compose_file="compose/$stack/docker-compose.yml" reason override old_id new_id
  local fetch=(pull --quiet) start=(up -d --no-deps) previous="$old_image" keep="$old_image"
  # A locally built image is rebuilt from the new pins and code; there is
  # nothing to pull. Started with --no-build (pull_policy: build would build it
  # a second time) and recreated whatever Compose thinks: the image name is
  # often the same.
  [[ "$built" == "yes" ]] && start=(up -d --no-deps --no-build --force-recreate)

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

  local fetched=0
  if [[ "$built" == "yes" ]]; then
    build_local "$compose_file" "$service" && fetched=1
  else
    docker compose "${ENV_ARGS[@]}" -f "$compose_file" "${fetch[@]}" "$service" && fetched=1
  fi
  if ((fetched)); then
    new_id="$(docker image inspect -f '{{.Id}}' "$new_image" 2>/dev/null || true)"
    if docker compose "${ENV_ARGS[@]}" -f "$compose_file" "${start[@]}" "$service" &&
      reason="$(wait_until_healthy "$container")"; then
      local changes
      changes="$(changes_since_pull "$stack")"
      notify updates "Updated $service" success \
        "Image" "$new_image${new_id:+ (${new_id:7:12})}" "Previous" "$previous" \
        ${changes:+"Changes" "$changes"}
      # The old image stays, to roll back to without a download; docker prune
      # (dockerprune/) keeps that one previous version and removes older ones.
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
    restart_changed_mounts "$PULLED_FROM"
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
      # update_pdf_gateway builds these together.
      [[ "$stack" == "pdf-gateway" && "$built" == "yes" ]] && continue
      # update_scanloom moves the whole stack.
      [[ "$stack" == "scanloom" ]] && continue
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

  update_pdf_gateway || failures=$((failures + 1))
  update_scanloom || failures=$((failures + 1))
  [[ "$DRY_RUN" == "1" ]] || report_stuck_updates

  echo "Checked $checked running service(s): $changed differ from the checkout, $failures failed."
  ((failures == 0))
}

# Sourcing the script exposes the functions without running an update.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
