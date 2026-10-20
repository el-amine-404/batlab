#!/usr/bin/env bash
# Gives Jellyfin's transcode folder its own fixed-size filesystem: an ext4 disk
# image on the SSD, loop-mounted at $VOLUMES_ROOT/jellyfin/transcodes from
# /etc/fstab. On 2026-10-20 a live TV stream nobody watched grew to 37.8 GB in
# that folder and nearly filled the root disk; now it can fill only this.
# The live TV guard (janitor/) closes such a stream long before. Run once on
# the server, with Jellyfin stopped:
#   make down STACK=jellyfin
#   sudo compose/jellyfin/setup-storage.sh [SIZE]    # default: 30G
#   make up STACK=jellyfin
# The compose file mounts transcodes/transcodes, a directory that exists only
# inside the image: if the image is not mounted, the container refuses to
# start rather than writing to the SSD underneath.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
set -a; . "$here/../.env"; set +a
: "${VOLUMES_ROOT:?set in compose/.env}"
size="${1:-30G}"
img="$VOLUMES_ROOT/jellyfin/transcodes.img"
mnt="$VOLUMES_ROOT/jellyfin/transcodes"
owner="$(stat -c %u:%g "$VOLUMES_ROOT/jellyfin/cache")"

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
if mountpoint -q "$mnt"; then
  echo "$mnt is already mounted"
  df -h "$mnt"
  exit 0
fi
if [ -n "$(docker ps -q -f name='^jellyfin$')" ]; then
  echo "Jellyfin is running: make down STACK=jellyfin first" >&2
  exit 1
fi

if [ ! -e "$img" ]; then
  fallocate -l "$size" "$img"
  # No reserved blocks: the whole size is for transcodes.
  mkfs.ext4 -q -m 0 -L jellyfin-transcodes "$img"
fi

mkdir -p "$mnt"
grep -qF "$img " /etc/fstab \
  || echo "$img $mnt ext4 loop,noatime,nofail 0 0" >> /etc/fstab
systemctl daemon-reload
mount "$mnt"
# Transcodes are throwaway: Jellyfin empties the folder when it starts, so
# nothing is moved over from the old one, which stays as the mount point.
install -d -o "${owner%:*}" -g "${owner#*:}" "$mnt/transcodes"
find "$VOLUMES_ROOT/jellyfin/cache/transcodes" -mindepth 1 -delete 2>/dev/null || true
df -h "$mnt"
ls -la "$mnt"
