# Media library scanning

Checks that everything under the media and torrent trees is the kind of file it
claims to be, carries no active content, and still decodes. Findings are written
as JSON reports and, optionally, moved into quarantine. Nothing is ever deleted.

## What each check does

| Script | Cost on this host | Finds |
| --- | --- | --- |
| `verify-types.py` | 142 s for 4.7k files | executables, scripts, archives, and extensions whose content does not match |
| `verify-media.py` | 32 s for 160 files | containers that are not video, or that contradict their filename |
| `verify-subtitles.py` | 1 s for 98 files | subtitles carrying markup that can fetch or execute |
| `scan-clamav.sh` | minutes, incremental | known malware in files clamd will actually read |
| `scan-virustotal.py` | one lookup per new carrier | files other people have already reported as malicious |
| `scan-yara.sh` | optional, off unless rules are set | matches against your own YARA rules |
| `deep-verify.py` | hours, rate limited | corruption that only appears when the file is read through |
| `watch.sh` | instant, per file | everything above, the moment a file lands |

`verify-media.py` reads container headers, so a 1 GB file and an 80 GB file both
cost about a second. `deep-verify.py` is the only script that reads whole files
or decodes frames, which is why it runs weekly against a rotating slice rather
than the whole library.

## Replaces the older media-scan.sh

This supersedes `batdots/scripts/user/media-scan.sh` and its two systemd user
units, which were stopped and removed. Everything it did is here, with three
corrections.

It quarantined any video shorter than 180 seconds, had no way to exempt a
directory, and was pointed at the whole of `media/`. That moved 283 phone clips
and 6 anime openings out of the library. Here the duration floor is 60 seconds,
`conf/excludes.txt` exempts subtrees from both the sweep and the watcher, and
quarantine is a dry run until `MEDIASCAN_QUARANTINE_APPLY=1`.

Its VirusTotal design was right and is kept as it was: the SHA-256 is sent, the
file never is, and a hash nobody has submitted comes back "unknown" rather than
"clean". Added on top is a verdict cache, so a rerun does not spend quota
re-asking about files it has already seen.

Its warning about a scanner that cannot read files reporting everything clean is
kept too, as a hard precondition: `scan-clamav.sh` scans a known-positive EICAR
file first and refuses to report a clean run if clamd fails to detect it.

## Sizing, measured on this host

```
ffprobe header, 80 GB file          1 s
demux 30 s sample                  <1 s
decode 30 s of 4K HEVC            114 s      3.8x slower than realtime
sequential read               191 MB/s
```

A full frame decode of one 4K film would take about 9.4 hours, so nothing
schedules one. A full demux of the same file takes about 7 minutes and catches
truncation and bit rot just as well, because it validates every frame boundary
without running the decoder.

## Why ClamAV only sees small files

`/etc/clamav/clamd.conf` sets `MaxFileSize 25M`, and clamd skips anything larger
without saying so. Pointing it at the whole library would report a clean 493 GB
while actually inspecting 26 GB. `scan-clamav.sh` therefore scans only files
below that limit, which is where scripts, subtitles, images, and stray
executables live anyway. Raising the limit does not make clamd inspect a film;
it only makes the skipping less visible.

## Quarantine and hardlinks

Radarr and Sonarr hardlink from `torrents/` into `media/`, so one file has two
paths and one inode. Moving only the path a scanner reported would leave the
other link live and still seeding. `quarantine.py` resolves every link to the
inode first and moves all of them, recording each move in
`<quarantine>/<date>/manifest.jsonl` with the inode, size, and SHA-256.

Quarantined files are moved rather than deleted, and chmod'd to `000`. Because
the move is a rename within the same filesystem, the copies stay hardlinked to
each other and consume no extra space.

`/mnt/storage/data/.quarantine` is already excluded from Restic by
`restic/conf/excludes.txt`.

## One-time installation on the server

```bash
sudo apt update
sudo apt install clamav-daemon clamav-freshclam ffmpeg file
```

Install the configuration and fill in the arr API keys:

```bash
sudo install -d -m 700 /etc/batlab-mediascan
sudo install -m 600 mediascan/conf/mediascan.env.example /etc/batlab-mediascan/mediascan.env
sudo editor /etc/batlab-mediascan/mediascan.env
```

Install the units:

```bash
sudo install -m 644 mediascan/systemd/*.service mediascan/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-mediascan-sweep.timer batlab-mediascan-deep.timer
sudo systemctl enable --now batlab-mediascan-watch.service
```

## Running by hand

```bash
sudo mediascan/scripts/sweep.sh                     # nightly checks, reports only
sudo mediascan/scripts/watch.sh                     # real-time, runs as a service
sudo mediascan/scripts/deep.sh --no-demux           # sample-decode new imports only
sudo mediascan/scripts/deep.sh --no-samples         # demux this week's slice only
```

Quarantine stays a dry run until `MEDIASCAN_QUARANTINE_APPLY=1` is set in the
env file. Review a report first:

```bash
sudo python3 mediascan/scripts/quarantine.py \
  --from-jsonl /var/lib/batlab-mediascan/reports/types.jsonl --problem EXECUTABLE
```

Add `--apply` once the list looks right.

## Replacing a bad file through the arr stack

`verify-media.py --remediate` deletes a flagged file and asks Radarr or Sonarr to
search for a replacement. It is off unless asked for, and it only acts on
verdicts that mean the file is wrong rather than merely unusual, so a short
runtime or a missing audio track never triggers it.

The arr containers mount `${DATA_ROOT}` at `/data`, so the paths they report are
not the paths on the host. Without `--arr-path-map` nothing matches and the run
silently does nothing:

```bash
python3 mediascan/scripts/verify-media.py /mnt/storage/data/media \
  --remediate-dry-run --arr-path-map /mnt/storage/data=/data
```

## Thermal note

This host has shut down under sustained all-core load. Every unit runs at
`Nice=19` with idle I/O scheduling, and the deep pass is additionally capped at
`CPUQuota=200%` with a two-hour budget. Do not remove those limits, and do not
build test fixtures by encoding video on this machine.
