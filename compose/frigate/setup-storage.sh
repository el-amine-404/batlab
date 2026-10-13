#!/usr/bin/env bash
# Gives Frigate's media (recordings, snapshots) its own fixed-size filesystem:
# an ext4 disk image on the SSD, loop-mounted at $VOLUMES_ROOT/frigate/media
# from /etc/fstab. Frigate deletes its oldest recordings when that filesystem
# runs low, so it can never fill the root disk. Run once on the server, with
# Frigate stopped:
#   make down STACK=frigate
#   sudo compose/frigate/setup-storage.sh [SIZE]    # default: 20G
#   make up STACK=frigate
# The compose file mounts media/frigate, a directory that exists only inside
# the image: if the image is not mounted, the container refuses to start
# rather than writing to the SSD underneath.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
set -a; . "$here/../.env"; set +a
: "${VOLUMES_ROOT:?set in compose/.env}"
size="${1:-20G}"
img="$VOLUMES_ROOT/frigate/media.img"
mnt="$VOLUMES_ROOT/frigate/media"

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
if mountpoint -q "$mnt"; then
  echo "$mnt is already mounted"
  df -h "$mnt"
  exit 0
fi
if [ -n "$(docker ps -q -f name='^frigate$')" ]; then
  echo "Frigate is running: make down STACK=frigate first" >&2
  exit 1
fi

if [ ! -e "$img" ]; then
  fallocate -l "$size" "$img"
  # No reserved blocks: the whole size is for Frigate.
  mkfs.ext4 -q -m 0 -L frigate-media "$img"
fi

# Move whatever Frigate already wrote into the new filesystem, so nothing is
# left hidden under the mount point.
mkdir -p "$mnt"
tmp="$(mktemp -d)"
mount -o loop "$img" "$tmp"
mkdir -p "$tmp/frigate"
cp -a "$mnt/." "$tmp/frigate/"
umount "$tmp"
rmdir "$tmp"
find "$mnt" -mindepth 1 -delete

grep -qF "$img " /etc/fstab \
  || echo "$img $mnt ext4 loop,noatime,nofail 0 0" >> /etc/fstab
systemctl daemon-reload
mount "$mnt"
df -h "$mnt"
ls -la "$mnt/frigate"
