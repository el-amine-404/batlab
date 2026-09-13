# Server backups with Restic

This directory contains the versioned configuration and scripts for backing up
the homelab server. Runtime credentials are deliberately stored outside Git.

## Backup scope

The paths in `conf/paths.txt` contain the homelab definitions, host
configuration, Docker application state, and Paperless documents. Replaceable
media, download queues, caches, Redis state, and downloaded AI models are
excluded by `conf/excludes.txt`.

Before every snapshot, `scripts/dump-databases.sh` creates a consistent custom
format PostgreSQL dump from `paperless-db`. The raw database volume remains in
the backup as a temporary second recovery path. It can be excluded later, after
a database restore has been tested successfully.

When `paperless-db` is stopped, the export is skipped rather than failing the
backup: a cleanly stopped database is consistent on disk, so the raw volume is
the recovery path, and the previous dump stays in the snapshot. Do not exclude
the raw volume while the Paperless stack is ever left stopped.

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
sudo systemctl enable --now batlab-restic-backup.timer batlab-restic-check.timer
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
03:30 and reads one seventh of the stored data each time.
