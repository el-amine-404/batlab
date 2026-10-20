# Backup prune

Removes the backup folders taken by hand before a migration or an upgrade,
once they are old enough that nobody will roll back to them.

Such a folder is named with its day: `~/pre-upgrade-20261017` (paperless,
immich and open-webui databases), `~/pre-subfeed-20261018` (clipfeed and
clipseerr before subfeed), `/mnt/docker-volumes/jellyfin-backup-10.11.3-20260915`.
Nobody remembered to delete them.

`scripts/backupprune.py`, nightly at 09:00 (`batlab-backupprune.timer`), reads
`conf/backups.txt`: a pattern and a number of days per line.

| Pattern | Kept |
| --- | --- |
| `~/pre-*` | 14 days |
| `${VOLUMES_ROOT}/*-backup-*` | 30 days |

The date is the last `YYYYMMDD` in the folder's name; a folder without one is
never removed, and neither is a symlink or a mount point. Name a new backup
folder after one of these patterns, with the day, and it goes on its own.

Each run that removed something posts to `#updates`, as "backup prune on lab2";
one that could not remove a folder (a file owned by root) says which:

```
Removed 1 old backup folder (190 MB)
Removed   /mnt/docker-volumes/jellyfin-backup-10.11.3-20260915  190 MB, from 2026-09-15
```

## Installation on the server

```bash
sudo install -m 644 backupprune/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-backupprune.timer
```

```bash
backupprune/scripts/backupprune.py --dry-run
python3 -m unittest discover -s backupprune/scripts/tests
```
