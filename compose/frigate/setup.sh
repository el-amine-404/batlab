#!/usr/bin/env bash
# Seeds Frigate's live config from compose/frigate/config.yml, once. After
# that Frigate owns the file (Settings UI, upgrade migrations), so an existing
# config is never overwritten. Run on the server before the first start:
#   compose/frigate/setup.sh
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
set -a; . "$here/../.env"; set +a
dir="$VOLUMES_ROOT/frigate"
for v in FRIGATE_DVR_HOST FRIGATE_RTSP_USER FRIGATE_RTSP_PASSWORD; do
  [ -n "${!v:-}" ] || { echo "$v is empty in compose/.env (see .env.example)" >&2; exit 1; }
done
# Frigate runs as root in its container; the directories only need to exist.
# Media gets its own filesystem from setup-storage.sh.
mkdir -p "$dir/config" "$dir/media"
if [ -e "$dir/config/config.yml" ]; then
  echo "kept $dir/config/config.yml (already there; edit it in Frigate's Settings)"
else
  install -m 600 "$here/config.yml" "$dir/config/config.yml"
  echo "wrote $dir/config/config.yml"
fi
