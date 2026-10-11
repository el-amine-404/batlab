#!/usr/bin/env bash
# Restarts iSponsorBlockTV when YouTube starts on the TV. It stays attached to
# the YouTube session it joined: once the TV is turned off and on, or YouTube
# restarts, that session is dead and it never notices (v2.11.0, seen
# 2026-10-11: an ad played with nothing listening). A restart joins the new
# session in seconds. Run every 20 s by batlab-isponsorblocktv-watch.timer.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
volumes="$(awk -F= '$1 == "VOLUMES_ROOT" { print $2 }' "$here/../.env")"
state_file="${STATE_FILE:-/run/batlab-isponsorblocktv-youtube}"

# The TV the tv-remote stack paired with; its address is kept current there.
ip="$(python3 -c 'import json, sys; print(next(iter(json.load(open(sys.argv[1])).values()))["ip"])' \
  "$volumes/tv-remote/tvs.json")"

# YouTube's DIAL state: running, stopped, or nothing when the TV is off.
now="$(curl -s -m 3 "http://$ip:8080/ws/apps/YouTube" | sed -n 's:.*<state>\([^<]*\)</state>.*:\1:p')"
now="${now:-off}"
prev="$(cat "$state_file" 2>/dev/null || echo unknown)"
echo "$now" >"$state_file"

if [[ "$now" == running && "$prev" != running && "$prev" != unknown ]]; then
  sleep 3 # let YouTube open its casting session first
  docker restart isponsorblocktv >/dev/null
  echo "YouTube started on the TV ($prev -> running): restarted isponsorblocktv"
fi
