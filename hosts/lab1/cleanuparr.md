# Cleanuparr on lab1

Cleanuparr keeps its settings in `/mnt/docker-volumes/cleanuparr/cleanuparr.db`,
not in this repository. These are the values set on 2026-09-13 and the ones
chosen because of this machine.

## Chosen for lab1's CPU

| Job | Schedule | Cleanuparr default |
| --- | --- | --- |
| Queue Cleaner | `0 0/10 * * * ?` (10 min) | 5 min |
| Malware Blocker | `0 0/5 * * * ?` (5 min) | every 5 seconds |
| Seeker proactive search | 30 min, round robin | 10 min |
| Blacklist Sync | `0 0 0/6 * * ?` (6 h) | hourly |

## Independent of hardware

- Queue Cleaner: failed import 3 strikes in include mode with seven patterns
  (`No files found are eligible for import`, `Found potentially dangerous file`,
  `Found executable file`, `Invalid season or episode`, `Unable to parse file`,
  `sample`, `Not an upgrade`); downloading metadata 3 strikes; stall rule
  `stalled` 0-100%, 3 strikes, reset on progress; slow rule `crawling` below
  100KB for 48 h, 0-90%, 3 strikes.
- Malware Blocker: remove the whole download if any file matches; Sonarr and
  Radarr use the official blacklist.
- Blacklist Sync writes the same list into qBittorrent's excluded file names,
  replacing a hand-made list whose `*.ha*`-style patterns skipped real films.
- Seeker instances: monitored only, below cutoff included, 3 active downloads,
  7-day minimum cycle.
- Notifications: Discord `#downloads` for removals, replacement grabs and
  cleaned downloads, webhook in `DISCORD_WEBHOOK_DOWNLOADS`.

## Download Cleaner

qBittorrent already enforces the seeding policy — ratio 1, 60 minutes, or 10
minutes idle — but its action is *Stop torrent*, so a finished download stays on
disk forever. Deleting it is Cleanuparr's job, because it is the only part of
the stack that can tell a download the library still uses from one it does not.

Radarr and Sonarr hardlink out of `torrents/` into `media/`, so a live download
has two links to one inode. When an upgrade replaces the library file, the
torrent copy drops back to one link. Zero remaining hardlinks is the signal that
nothing in the library points at it any more.

| Setting | Value |
| --- | --- |
| Download Cleaner | enabled, hourly, `0 0 * * * ?` |
| Unlinked handling | enabled, categories `movies` and `tv` |
| Unlinked marking | tag, not category |
| Tag | `unlinked` |
| Seeding rule | categories `movies` and `tv`, tags (any) `unlinked`, max seed time 1 h, max inactive days 7, action Delete with source files |

Tagging rather than moving the category leaves Radarr's and Sonarr's own
category mapping alone.

A download that never imported has no hardlinks either, and is indistinguishable
from one an upgrade replaced. The seven idle days are the grace period:
`import-audit` reports it as left behind within three hours, which leaves a week
to import it by hand before Cleanuparr removes it.

Cleanuparr mounts `torrents/` read-only and at the same path qBittorrent uses.
Both matter: the hardlink count is read from the file, and the delete is done by
qBittorrent over its API. A different mount path makes every download look
unlinked.
