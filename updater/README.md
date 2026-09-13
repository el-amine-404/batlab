# Automatic image updates

Image versions are pinned in `compose/versions.env`, which is tracked in Git.
Two pieces keep those pins current without hand-editing:

- **Renovate** (GitHub app, `renovate.json`) watches every `IMAGE_`/`TAG_` pair
  and opens a pull request when a newer tag exists and has been public for three
  days. For the low-risk, security-relevant apps in its automerge rule it merges
  minor and patch bumps on its own; majors and every other image wait for review.
- **The updater** (`scripts/update.sh`, nightly on the server) fast-forwards the
  checkout, compares each running container's image with what the checkout now
  says, and recreates the services listed in `conf/auto-services.txt`. It waits
  for the healthcheck, rolls back to the previous image if the new one does not
  come up, and removes the old image once the new one is healthy.

```
Renovate PR ── automerge? ──▶ main ── nightly git pull ──▶ running image differs?
                  │                                        │
                  └─ no: you review and merge               ├─ listed: pull, recreate, wait healthy
                                                            │          ok: #downloads, else roll back + #alerts
                                                            └─ not listed: one #downloads note, you run make up
```

Stopped containers are never started, so services the data disk guard or you
stopped stay stopped. A tag that failed once is not retried until a newer one
lands. The updater refuses to pull over local changes and says so in `#alerts`.

## Choosing what updates itself

A service updates itself only when it appears in **both** places:

1. the automerge `packageRules` entry in `renovate.json` (by image name), and
2. `conf/auto-services.txt` (by compose service name).

Leave DNS, the reverse proxy, the VPN, databases, and anything with schema
migrations out of both. Majors never automerge regardless of the list.

## One-time installation on the server

Renovate needs its GitHub app installed on the repository in interactive mode,
with "Allow auto-merge" enabled under the repository's pull request settings.

The updater runs as `potato`, reads its Discord webhooks from `compose/.env`, and
keeps state in `~/.local/state/batlab-updater`. Only the units need root:

```bash
sudo install -m 644 updater/systemd/batlab-updater.service updater/systemd/batlab-updater.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-updater.timer
```

## Running by hand

```bash
UPDATER_DRY_RUN=1 updater/scripts/update.sh    # report differences, change nothing
updater/scripts/update.sh                       # what the timer runs
journalctl -u batlab-updater.service -n 50 --no-pager
```

To retry a tag that failed, remove its line from
`~/.local/state/batlab-updater/failed`.
