# Backup & Restore

This repo only tracks **declarative config** (compose files, Caddyfiles, homepage
config, …). The two things that actually carry state are deliberately **not** in
git and are protected by [Restic][restic] instead, driven by the
[batdots][batdots] repo:

| What                       | Where                  | In git? | Backed up by                |
| :------------------------- | :--------------------- | :------ | :-------------------------- |
| Secrets (`.env`)           | `/srv/docker-compose`  | ❌ no   | Restic (`/srv/docker-compose`) |
| Runtime data, DBs, volumes | `/mnt/docker-volumes`  | ❌ no   | Restic (`/mnt/docker-volumes`) |
| Service config             | this repo              | ✅ yes  | git                         |

> Because `.env` is gitignored, **git is not a backup of your secrets** — Restic
> is. If you rebuild the server, restore `/srv/docker-compose/.env` from Restic
> (or re-create it from `.env.example`).

## How it runs

The Restic wiring lives in [batdots][batdots] (`scripts/user/restic-*.sh`,
`lib/backup.sh`) and is configured per-host in `local/env.sh` (gitignored).
On this server it is scheduled by systemd user timers:

| Timer                   | Schedule              | Job                                            |
| :---------------------- | :-------------------- | :--------------------------------------------- |
| `restic-backup`         | nightly, 02:00        | snapshot every reachable repo, then prune      |
| `restic-check`          | weekly, Sun 03:30     | verify a subset of pack data                   |
| `restic-test-restore`   | monthly, 1st 04:00    | restore a sentinel file to prove restores work |

- **Backup paths** include `/srv/docker-compose` and `/mnt/docker-volumes`.
- **Repos** follow a 3-2-1 layout: an air-gapped local/USB repo (included when
  plugged in) plus an offsite repo; each reachable repo receives every snapshot.
- **Retention** (prune): keep 7 daily, 4 weekly, 12 monthly, 3 yearly.
- **Excludes** (`apps/restic/restic_ignore` in batdots) skip caches, logs,
  `MediaCover/`, app-internal `Backups/`, and the bulky `jellyfin/data`,
  `adguardhome/data`, and `caddy/data` dirs — so snapshots stay lean.

## Manual operations

```bash
# Run a backup now (all reachable repos)
~/dotfiles/scripts/user/restic-backup.sh

# Dry run (no writes)
DRY_RUN=1 ~/dotfiles/scripts/user/restic-backup.sh

# Verify repo integrity
~/dotfiles/scripts/user/restic-check.sh

# List snapshots (uses RESTIC_REPOSITORY / RESTIC_PASSWORD_FILE from env.sh)
restic snapshots
```

## Restore

After provisioning a fresh server (see [INSTALL.md](./INSTALL.md)) but **before**
starting the stacks, restore state into place:

```bash
# Pick the snapshot you want
restic snapshots

# Restore everything from the latest snapshot to its original paths
restic restore latest --target /

# …or restore just one service's volume
restic restore latest --target / --include /mnt/docker-volumes/jellyfin

# …or recover only the secrets file
restic restore latest --target / --include /srv/docker-compose/.env
```

Then bring the stacks up as usual. Restored bind-mount data keeps each app's
databases, settings, and API keys, so services come back exactly as they were.

[restic]: https://restic.net
[batdots]: https://github.com/el-amine-404/batdots
