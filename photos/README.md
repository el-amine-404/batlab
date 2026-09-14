# Photos and personal files

Irreplaceable personal data lives in its own top-level trees on the data disk,
outside `media/`: nothing here is re-downloadable, so it is backed up on its own,
the arr stack's media conventions do not apply, and mediascan's type checks do
not mistake phone clips for fake films.

```
/mnt/storage/data/
├── photos/
│   ├── library/         the archive, filed by theme; Immich's external library, read-only
│   │   └── BIRTHDAYS/ FAMILLY/ WEDDINGS/ …   themes carried over from HDD_500_11
│   ├── inbox/           the one place to rename, sort and deduplicate before filing; not in Immich
│   │   └── iphone-2025-2026/                 iPhone backups waiting to be filed
│   └── private/         kept, never indexed by Immich
└── files/               documents and other files to host
    ├── family/
    └── archives/        notes and recovered items from retired drives
```

## Workflow

```
iPhone ─ Immich app backup ─▶ /mnt/storage/data/immich   (Immich-managed, never edited by hand)
                                       │  copy out
                                       ▼
                                 photos/inbox/            rename, sort, deduplicate
                                       │  mv -n into a theme
                                       ▼
                             photos/library/<theme>/
                                       │  Immich rescans the external library
                                       ▼
                    the photo shows from the archive; the uploaded copy is removed from Immich
```

Immich's upload location is not `inbox/`: Immich names, moves and owns its
upload storage, while `inbox/` and `library/` are edited by hand.

`inbox/` stays out of Immich on purpose. Immich sees a moved file as a deletion
plus a new asset, so indexing photos that are still going to be filed would lose
their faces and albums; until they are filed, Immich shows the uploaded copy.

File with `mv -n`, which never replaces an existing file, and move a photo
together with its `.xmp` sidecar and, for Live Photos, the `.mov` of the same
name. Whatever `mv -n` leaves behind already has a namesake in the theme: either
a duplicate, or a different shot from the same second to rename with `_01`.

## Folders by visit

Inside a place or event, keep one subfolder per visit: `YYYY-MM-DD` for one
day, `YYYY-MM-DD_TO_YYYY-MM-DD` for consecutive days, optionally followed by
`_DESCRIPTION` (`2022-09-02_ALMINA-BEACH`). A place visited only once can stay
flat. Once files are named by capture time, `scripts/group-by-date.py` builds
those folders from the names:

```bash
photos/scripts/group-by-date.py <folder>            # preview
photos/scripts/group-by-date.py <folder> --apply    # move, never overwriting
```

It moves only dated files lying directly in the folder, takes their `.xmp`
sidecars along, adds days to an existing visit folder that covers them, and
leaves anything it would have to overwrite where it is. Group a folder before
Immich indexes it.

## Naming files by capture time

`scripts/organize-media.py` names every photo and video
`YYYY-MM-DD_HHh-MMm-SSs.ext` by the local time where it was taken, collision
free and reversibly. Run it before Immich indexes a folder: a rename afterwards
looks to Immich like a deletion plus a new asset, losing faces and albums.

```bash
S=photos/scripts/organize-media.py
L=/mnt/storage/data/photos/library
$S plan $L --recursive --sample 400                                   # look first
$S plan $L --recursive --rules $L/.organize/folder-rules.conf
<bundle>/apply.sh --accept-weak --allow-skipped   # rename, write sidecars, flag weak dates
<bundle>/rollback.sh                # undo all of it
$S plan /mnt/storage/data/photos/inbox --recursive                    # new arrivals, before filing
```

Plan `library/` and `inbox/` separately: folder rules are relative to the folder
being planned. They name private folders, so they live with the archive in
`<root>/.organize/folder-rules.conf`, not in this repository;
`conf/folder-rules.conf` documents the syntax. Rename in `inbox/` before filing, so files arrive in a theme
already named and a namesake there is a real duplicate or same-second shot.

On a host that overheats, add the temperature guard from its `hosts/<profile>/README.md`.

Requirements: Python 3.9 or later and `exiftool` (`sudo apt install
libimage-exiftool-perl`); the plan refuses to start without it. The optional
`timezonefinder` package gives exact times to photos with GPS but no time zone
tag, typically from cameras and older phones abroad; without it they get the
folder rule or `Africa/Casablanca`. Debian has no package for it, so give the tool a
virtual environment and plan with its Python (apply and rollback do not need it):

```bash
sudo apt install python3-venv
python3 -m venv ~/.local/share/organize-media
~/.local/share/organize-media/bin/pip install timezonefinder
~/.local/share/organize-media/bin/python photos/scripts/organize-media.py plan …
```

The plan header shows `GPS zone lookup: available` once it is found.

Each plan bundle holds `plan.tsv` (every file: old and new name, local time,
UTC offset, confidence, evidence, rejected dates) and `review.tsv` (assumed and
weak dates grouped by folder, the place to decide on folder rules).

How the time is chosen, most reliable first:

| Confidence | Evidence |
| --- | --- |
| exact | local time with its offset (iPhone photos and videos); a local time whose offset the GPS clock in the file proves; a UTC time in the zone at the file's GPS position (needs the optional `timezonefinder` package) |
| assumed | a zoneless time read in the folder's zone or `Africa/Casablanca`; a UTC video time converted the same way; a full date and time in the file name |
| weak | a day from the file name (WhatsApp) or a dated folder (`2022-OCT-01`, `2015-09-06_TO_…`); with `--use-modified-time`, the file's modified time |

The modified time is not used by default: on files that went through copies and
old drives it is the day they were copied (57 HDD_500_11 files shared one minute
of 2008). A file with no other date is skipped and keeps its name; pass
`--allow-skipped` to apply the rest. Use `--use-modified-time` only for a source
whose files were never copied, such as a fresh phone export.

Times are never read in the zone of the machine running the tool, which is how
the iPhone videos from France had been named an hour early. Daylight saving and
Morocco's Ramadan offset come from the system zone database.

Dates that cannot be real are rejected and the next source is used: empty and
1904/1970 container dates, camera reset dates (1 January at midnight), anything
before `--min-year` (1990) or in the future. Files already named with such a
date are renamed.

A day without a trustworthy time is named `YYYY-MM-DD_date-only_NN.ext`.

Weak dates need `--accept-weak` to apply. They are flagged twice: the tag
`review/date-unverified` in the file's XMP sidecar, visible under Tags in
Immich, and a row in `<root>/.organize/unverified.tsv`. To fix one, add a folder
rule and plan again; the new plan drops the flag.

Sidecars (`<name>.xmp`) are written only where software reading the media file
alone would show a different time: weak dates, UTC video times, names, folder
rules and clock shifts. They hold the date with its offset and the original file
name. Media bytes are never modified, and a file that already has a sidecar from
another tool is skipped rather than overwritten.

## Importing a drive

`scripts/import-drive.sh` copies folders from a drive plugged into a laptop into
these trees. What goes where is described by a drive file in
`~/.config/batlab-import/<drive>.conf`, kept out of this repository because it
describes one person's disk; start from `conf/import-drive.example.conf`.

```bash
S=photos/scripts/import-drive.sh
$S <drive> --dry-run      # plan only
$S <drive>                # copy, then verify
$S <drive> --verify-only
```

It refuses any disk but the one whose filesystem UUID the drive file names, is
resumable, checksums every file as it lands, and ends with a full xxh128
comparison of both sides. Symlinks and special files stop the run instead of
being skipped. Server-side files organize-media.py adds in `photos/library` and
`photos/inbox` (`*.xmp`, `.organize/`) are not reported as extra; anywhere else
they are. The end of every copy or verification is posted
to Discord through `DISCORD_WEBHOOK_ALERTS` in `compose/.env`. On a host that
overheats, add the temperature guard from its `hosts/<profile>/README.md`.

Keep the source drive untouched until the copy is verified and offsite backup of
`photos/` exists.
