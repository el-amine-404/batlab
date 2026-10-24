#!/usr/bin/env bash

set -Eeuo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/state"

cat >"$tmp/watchdog.env" <<EOF
WATCHDOG_DATA_ROOT=/mnt/test-data
EOF

cat >"$tmp/bin/mountpoint" <<'EOF'
#!/bin/sh
exit 0
EOF

cat >"$tmp/bin/python3" <<'EOF'
#!/bin/sh
exit 0
EOF

cat >"$tmp/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -u
state="${FAKE_DOCKER_STATE:?}"
cmd="$1"
shift
case "$cmd" in
ps)
  printf 'alpha\nbeta\n'
  ;;
inspect)
  format=""
  if [[ "${1:-}" == -f ]]; then format="$2"; shift 2; fi
  if [[ "$format" == *'.Name'* ]]; then
    for name in "$@"; do
      running="$(<"$state/$name")"
      printf '/%s unless-stopped %s ok /mnt/test-data/%s\n' "$name" "$running" "$name"
    done
  else
    name="${1:?}"
    running="$(<"$state/$name")"
    printf '%s\n' "$running"
  fi
  ;;
start)
  name="${1:?}"
  if [[ "$name" == beta && ! -e "$state/beta-tried" ]]; then
    touch "$state/beta-tried"
    exit 1
  fi
  printf 'true\n' >"$state/$name"
  printf '%s\n' "$name"
  ;;
*) exit 2 ;;
esac
EOF
chmod +x "$tmp/bin/"*
printf 'false\n' >"$tmp/state/alpha"
printf 'false\n' >"$tmp/state/beta"
printf 'alpha\nbeta\n' >"$tmp/state/stopped-containers"

export PATH="$tmp/bin:$PATH"
export WATCHDOG_CONFIG_FILE="$tmp/watchdog.env"
export WATCHDOG_STATE_DIR="$tmp/state"
export WATCHDOG_LOCK_FILE="$tmp/guard.lock"
export FAKE_DOCKER_STATE="$tmp/state"

if "$REPO/watchdog/scripts/data-guard.sh" start; then
  echo "first partial start unexpectedly succeeded" >&2
  exit 1
fi
[[ "$(<"$tmp/state/alpha")" == true ]]
[[ "$(<"$tmp/state/beta")" == false ]]
[[ "$(<"$tmp/state/stopped-containers")" == beta ]]

"$REPO/watchdog/scripts/data-guard.sh" start
[[ "$(<"$tmp/state/beta")" == true ]]
[[ ! -e "$tmp/state/stopped-containers" ]]
echo "data guard transactional restart test passed"

# Bash accepts `local` at parse time even when it appears outside a function;
# it then fails only on the rare recovery-exhausted path.
if awk '
  /^[[:space:]]*[a-zA-Z_][a-zA-Z0-9_]*\(\)[[:space:]]*\{/ { depth++ }
  /^[[:space:]]*}/ && depth { depth-- }
  !depth && /^[[:space:]]*local([[:space:]]|$)/ { print; bad=1 }
  END { exit bad }
' "$REPO/watchdog/scripts/revive-data-disk.sh"; then
  :
else
  echo "revive-data-disk.sh declares local variables outside a function" >&2
  exit 1
fi
