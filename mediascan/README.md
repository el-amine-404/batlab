# Media library scanning

Checks that everything under the media and torrent trees is the kind of file it
claims to be, carries no active content, and still decodes. Findings are written
as JSON reports and, optionally, moved into quarantine. Nothing is ever deleted.

## What each check does

| Script | Cost on lab1 | Finds |
| --- | --- | --- |
| `verify-types.py` | 142 s for 4.7k files | executables, scripts, archives, and extensions whose content does not match |
| `verify-media.py` | 32 s for 160 files | containers that are not video, or that contradict their filename |
| `verify-subtitles.py` | 1 s for 98 files | subtitles carrying markup that can fetch or execute |
| `scan-clamav.sh` | minutes, incremental | known malware in files clamd will actually read |
| `scan-virustotal.py` | one lookup per new carrier | files other people have already reported as malicious |
| `scan-yara.sh` | optional, off unless rules are set | matches against your own YARA rules |
| `deep-verify.py` | hours, rate limited | corruption that only appears when the file is read through |
| `verify-embedded.py` | one ffprobe per container, minutes for the ones that carry something | fonts, cover art and subtitle tracks that are not what the container says they are, or that carry known malware |
| `watch.sh` | instant, per file | everything above, the moment a file lands |
| `selftest.py` | 2 min | nothing in the library: it proves the checks above still catch what they should |

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

## Sizing, measured on lab1

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

## Proving the checks still work

The EICAR precondition in `scan-clamav.sh` is there because an engine that
cannot read its input reports a clean library, which looks exactly like a clean
library. Every other check has the same failure mode, and a `file` database or
an ffmpeg version can change what they see without anything here changing.

`selftest.py` builds, for each check, a file that must be flagged and a file
that must not, and compares the verdicts against the quarantine list in
`sweep.sh`:

```bash
mediascan/scripts/selftest.py          # needs ffmpeg and file; no root, no config
mediascan/scripts/selftest.py --keep   # leave the fixtures behind to look at
```

Fixtures are built in a temporary directory outside the library, so the watcher
never sees them and nothing in `roots.txt` is touched. Run it after changing a
verifier, and on the server after a distribution upgrade, since that is where
the engines that decide the verdicts actually live. `clamd` is not reachable
from a laptop, so a run there skips it and says so; a skipped check proves
nothing.

A detection nothing acts on is a failure too. If a fixture is flagged with a
verdict missing from `sweep.sh`'s `--problem` list, the file would be reported
every night and left exactly where it is.

## Cover art is a video stream

Matroska carries a poster as an attachment named `cover.jpg`, and ffmpeg hands
it back as a second video stream with `attached_pic` set. A poster is usually
taller than the film is wide, so choosing the largest video stream chooses the
poster: a correct 1080p h264 remux reported itself as `mjpeg`, and every remux
with cover art would have. `verify-media.py` selects on the disposition instead,
and `deep-verify.py` decodes `0:V:0` rather than `0:v:0` so the sample decode
reads the feature and not a single JPEG.

A container holding nothing but a poster is `COVER_ART_ONLY`, which is reported
and not quarantined. `NOT_VIDEO` stays what it was, a container with no video
stream at all. The difference matters because quarantine moves every hardlink of
what it acts on, and an audiobook in a `.mkv` is misfiled rather than dangerous.

## What a container carries is not what it claims

The checks above answer whether a file is the kind of file its name claims. They
say nothing about what is inside it. A Matroska container holds fonts, cover art
and subtitle tracks, and each is later handed to something that parses it —
freetype, an image decoder, libass — with nothing having read the bytes first.

`verify-media.py` does look at attachments, but it reads the `mimetype` tag,
which is a string the file supplies about itself: declaring `font/ttf` over an
ELF binary passes it. Cover art is not reachable from that check at all, because
Matroska carries it as an attachment that ffmpeg re-presents as a video stream.

`verify-embedded.py` extracts the parts and judges them on their bytes:

| Verdict | Means |
| --- | --- |
| `MIME_LIE` | the declared mimetype and the actual content disagree |
| `NOT_A_FONT` | declared a font, but carries no font header |
| `NOT_AN_IMAGE` | cover art that is not an image |
| `APPENDED_DATA` | bytes after the image's end marker, where a second payload hides |
| `DANGEROUS_ATTACHMENT` | an attachment with an extension nothing should carry |
| `MALWARE` | clamd matched the extracted part |

Text subtitle tracks are run through `verify-subtitles.py` itself, so an `.ass`
track inside a container is judged exactly like one beside it. Image subtitles
are bitmaps and are skipped.

Like `scan-clamav.sh`, it refuses to report clean if clamd cannot detect the
EICAR probe. Pass `--no-clamav` to run the structural checks alone.

**These verdicts are reported, not quarantined.** Nothing merges
`embedded.jsonl` into the quarantine pass. A detection does not get to move
files on the strength of its first week in service.

It runs from `deep.sh`, because extraction means reading the container and the
deep pass is already doing that. A container carrying nothing costs one
`ffprobe`, so it walks the whole library rather than the weekly slice, bounded by
`MEDIASCAN_EMBEDDED_TIME_BUDGET` (one hour by default).

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

Then install the host drop-ins, if the profile has any, as described in
`hosts/<profile>/README.md`.

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

## The scans run unprivileged

They parse files chosen by whoever made the torrent. Doing that as root on the
host means one parser bug in ffmpeg or `file` is root on the host, outside any
container. Nothing here needs root: the library is `potato:potato` and clamd is
reached through a world-writable socket.

`systemd/hardening.conf` is a drop-in, not an edit to the units, so it can be
removed without touching them. It drops the scans to `potato`, clears every
capability, makes the filesystem read-only apart from the state directory and
the library, and restricts syscalls and address families.

Two things must be handed over first, or every run exits on the first line:

```bash
sudo chown -R potato:potato /var/lib/batlab-mediascan
sudo chgrp potato /etc/batlab-mediascan /etc/batlab-mediascan/mediascan.env
sudo chmod 750 /etc/batlab-mediascan
sudo chmod 640 /etc/batlab-mediascan/mediascan.env
```

Prove the sandbox on the selftest unit before any real scan runs inside it:

```bash
sudo install -m 644 mediascan/systemd/batlab-mediascan-selftest.service /etc/systemd/system/
sudo install -d -m 755 /etc/systemd/system/batlab-mediascan-selftest.service.d
sudo install -m 644 mediascan/systemd/hardening.conf \
  /etc/systemd/system/batlab-mediascan-selftest.service.d/
sudo systemctl daemon-reload
sudo systemctl start batlab-mediascan-selftest.service
journalctl -u batlab-mediascan-selftest.service -n 60 --no-pager
```

`selftest.py` builds containers with ffmpeg, reads them with ffprobe, types
files with `file`, scans with clamd, and moves hardlinked files — inside the
same sandbox the scans will run in. It must end `0 failed`. The same drop-in is
installed against the selftest unit so the two cannot drift apart.

Only once it passes, apply it to the scans themselves:

```bash
for unit in sweep deep watch; do
  sudo install -d -m 755 /etc/systemd/system/batlab-mediascan-$unit.service.d
  sudo install -m 644 mediascan/systemd/hardening.conf \
    /etc/systemd/system/batlab-mediascan-$unit.service.d/
done
sudo systemctl daemon-reload
sudo systemctl restart batlab-mediascan-watch.service
sudo systemctl start batlab-mediascan-sweep.service   # a real run, on the real library
```

A running watcher keeps its old privileges until it is restarted, so that
restart is what actually moves it into the sandbox.

Undo all of it with:

```bash
sudo rm /etc/systemd/system/batlab-mediascan-*.service.d/hardening.conf
sudo systemctl daemon-reload
```

`systemd-analyze security batlab-mediascan-sweep.service` scores it before and
after. `MemoryDenyWriteExecute` is deliberately absent: some codecs allocate
executable pages, so it would break the decode rather than the attack.

## Host limits

The units run at `Nice=19` with idle I/O on every machine. Anything sized for a
particular host, such as a CPU quota or a temperature cutoff for the deep pass,
is a systemd drop-in under `hosts/<profile>/systemd/`; lab1's is described in
`hosts/lab1/README.md`. `deep.sh` stops starting new files above
`MEDIASCAN_MAX_CPU_TEMP` read from the hwmon sensor named in
`MEDIASCAN_CPU_TEMP_SENSOR`, and does nothing when they are unset.

Do not build test fixtures by encoding video on a machine that overheats.
