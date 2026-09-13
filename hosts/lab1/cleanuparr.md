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
