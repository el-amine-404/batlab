#!/usr/bin/env bash
# Writes the volume's config.json from the template, with the TV's YouTube
# screen id taken from the tv-remote stack's pairing (made the first time
# YouTube was open on the TV with the remote running). Run on the server:
#   compose/isponsorblocktv/setup.sh
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
set -a; . "$here/../.env"; set +a
dir="$VOLUMES_ROOT/isponsorblocktv"
pairing="$(ls "$VOLUMES_ROOT"/tv-remote/youtube-*.json 2>/dev/null | head -1)"
[ -n "$pairing" ] || { echo "no tv-remote YouTube pairing: open YouTube on the TV with the remote running" >&2; exit 1; }
screen_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["screenId"])' "$pairing")"
mkdir -p "$dir"
umask 077
python3 - "$here/config.json.template" "$dir/config.json" "$screen_id" <<'PY'
import json, sys
template, out, screen_id = sys.argv[1:]
cfg = json.load(open(template))
cfg["devices"][0]["screen_id"] = screen_id
json.dump(cfg, open(out, "w"), indent=2)
PY
echo "wrote $dir/config.json"
