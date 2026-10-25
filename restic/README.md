# Server backups with Restic

This directory contains the versioned configuration and scripts for backing up
the homelab server. Runtime credentials are deliberately stored outside Git.

## Backup scope

The paths in `conf/paths.txt` contain the homelab definitions, host
configuration, Docker application state, Paperless documents, and the PDF
gateway's archive (`/mnt/storage/data/documents/pdf-archive`: the PDFs with
their sidecars). Replaceable
media, download queues, caches, Redis state, and downloaded AI models are
excluded by `conf/excludes.txt`.

Secrets are included inside the encrypted Restic repository: `compose/.env`,
root-owned service configuration below `/etc`, SSH material, and rclone's
configuration. Git continues to ignore them. Keep the Restic repository
password in an independent password manager: the copy inside the encrypted
repository cannot unlock that repository after a total host loss.

Before every snapshot, `scripts/dump-databases.sh` creates consistent custom
format PostgreSQL dumps from `paperless-db`, `immich-db` and `pdf-gateway-db`.
The restore test also restores one archived PDF and checks it against the
SHA-256 in its sidecar. The raw database
volumes remain in the backup as secondary recovery paths.

When either database is stopped, its export is skipped rather than failing the
backup: a cleanly stopped database is consistent on disk, so its raw volume is
the recovery path, and the previous dump stays in the snapshot.

Then `scripts/dump-sqlite.py` copies every SQLite database in the Docker
volumes (the apps' own: the *arr apps, Jellyfin, videodrop, clipseerr…) into
`/mnt/docker-volumes/database-dumps/sqlite/<its path>`, through SQLite's backup
API: one transaction each, so the copy is whole even while the app writes. The
live files restic also reads can be caught mid-write (the database, its -wal
and its -shm read at different moments). Each copy is made by a process running
as the file's owner (no root-owned -wal or -shm left for the app), checked, and
only then replaces the last one. Caches and logs (`logs.db`, `cache/`) are left
out, as in `conf/excludes.txt`. A database that fails keeps its last good copy
and fails the run, after the snapshot.

To restore an app's database: stop the app, then put the copy back in place of
the live file and delete its `-wal` and `-shm`:

```sh
make down STACK=videodrop
sudo restic restore latest --target /tmp/r \
  --include /mnt/docker-volumes/database-dumps/sqlite/videodrop/videodrop.db
sudo install -o potato -g potato -m 600 \
  /tmp/r/mnt/docker-volumes/database-dumps/sqlite/videodrop/videodrop.db \
  /mnt/docker-volumes/videodrop/videodrop.db
sudo rm -f /mnt/docker-volumes/videodrop/videodrop.db-wal /mnt/docker-volumes/videodrop/videodrop.db-shm
make up STACK=videodrop
```

## One-time installation on the server

Install the required packages:

```bash
sudo apt update
sudo apt install restic rclone
```

Configure the root account's rclone remote because the system service runs as
root and must be able to read root-owned Docker volumes:

```bash
sudo rclone config
sudo rclone lsd gdrive_personal:
```

Install the configuration and create the Restic password file:

```bash
sudo install -d -m 700 /etc/batlab-restic
sudo install -m 600 restic/conf/restic.env.example /etc/batlab-restic/restic.env
sudo editor /etc/batlab-restic/restic.env
sudo sh -c 'umask 077; editor /etc/batlab-restic/restic-password'
```

Keep a second copy of the password in a password manager. It cannot be
recovered from the repository.

Initialize the repository once:

```bash
sudo bash -c 'set -a; source /etc/batlab-restic/restic.env; set +a; restic init'
```

Install and enable the units:

```bash
sudo install -m 644 restic/systemd/batlab-restic-backup.service /etc/systemd/system/
sudo install -m 644 restic/systemd/batlab-restic-backup.timer /etc/systemd/system/
sudo install -m 644 restic/systemd/batlab-restic-check.service /etc/systemd/system/
sudo install -m 644 restic/systemd/batlab-restic-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-restic-backup.timer batlab-restic-check.timer \
  batlab-restic-restore-test.timer
```

The unit paths assume the repository is `/home/potato/batlab`. Adjust the two
service files if the checkout is elsewhere.

## Test before relying on it

Run a database export and snapshot manually:

```bash
sudo /home/potato/batlab/restic/scripts/dump-databases.sh
sudo /home/potato/batlab/restic/scripts/backup.sh
sudo systemctl status batlab-restic-backup.service
sudo journalctl -u batlab-restic-backup.service -n 100 --no-pager
```

Inspect and verify the repository:

```bash
sudo /home/potato/batlab/restic/scripts/check.sh
sudo bash -c 'set -a; source /etc/batlab-restic/restic.env; set +a; restic snapshots'
```

Finally, restore at least the Paperless dump into a temporary directory and
verify that it is non-empty:

```bash
restore_dir="$(sudo mktemp -d)"
sudo bash -c 'set -a; source /etc/batlab-restic/restic.env; set +a; \
  restic restore latest --target "$1" \
  --include /mnt/docker-volumes/database-dumps/paperless.dump' \
  bash "$restore_dir"
sudo ls -lh "$restore_dir/mnt/docker-volumes/database-dumps/paperless.dump"
sudo rm -r -- "$restore_dir"
```

The backup timer runs daily at 02:00. The repository check runs every Sunday at
03:30 and reads one seventh of the stored data each time. On the first day of
each month, `batlab-restic-restore-test.timer` restores the Paperless and Immich
PostgreSQL dumps, NetAlertX SQLite database, `compose/.env`, and repository
version pins from the newest off-site snapshot, then validates every restored
artifact without printing secret values. Backup and
restore-test results go to the `backups` notification channel with the failed
stage and recovery command when action is required.
