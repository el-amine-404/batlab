#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
load_restic_config
require_command restic

restic unlock
# The nightly backup holds the repository's lock while it runs, and both timers
# catch up together after a reboot (Persistent=true): wait for it rather than
# fail the week's check.
restic check --retry-lock 2h --read-data-subset="${RESTIC_CHECK_SUBSET:-1/7}"
