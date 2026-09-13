# Photos and personal files

Irreplaceable personal data lives in its own top-level trees on the data disk,
outside `media/`: nothing here is re-downloadable, so it is backed up on its own,
the arr stack's media conventions do not apply, and mediascan's type checks do
not mistake phone clips for fake films.

```
/mnt/storage/data/
├── photos/
│   ├── library/         the archive; Immich's external library, mounted read-only
│   │   ├── BIRTHDAYS/ FAMILLY/ WEDDINGS/ …   themes carried over from HDD_500_11
│   │   └── _to-merge/iphone-2025-2026/       iPhone folders waiting to be filed into the themes
│   ├── inbox/           sorted but not yet filed; also visible in Immich
│   └── private/         kept, never indexed by Immich
└── files/               documents and other files to host
    ├── family/
    └── archives/        notes and recovered items from retired drives
```

## Workflow

```
iPhone ─ Immich app backup ─▶ /mnt/storage/data/immich   (Immich-managed, never edited by hand)
                                       │  sort
                                       ▼
                        photos/inbox/ or photos/library/<theme>/
                                       │  Immich rescans the external library
                                       ▼
                    the photo shows from the archive; the uploaded copy is removed from Immich
```

Immich's upload location is not `inbox/`: Immich names, moves and owns its
upload storage, while `inbox/` and `library/` are edited by hand.

## Naming files by capture time

`scripts/organize-media.py` names every photo and video
`YYYY-MM-DD_HHh-MMm-SSs.ext` by the local time where it was taken, collision
free and reversibly. Run it before Immich indexes a folder: a rename afterwards
looks to Immich like a deletion plus a new asset, losing faces and albums.

```bash
S=photos/scripts/organize-media.py
$S plan /mnt/storage/data/photos/library --recursive --sample 400     # look first
$S plan /mnt/storage/data/photos/library --recursive --rules photos/conf/folder-rules.conf
<bundle>/apply.sh --accept-weak     # rename, write sidecars, flag weak dates
<bundle>/rollback.sh                # undo all of it
```

On a host that overheats, add the temperature guard from its `hosts/<profile>/README.md`.

Each plan bundle holds `plan.tsv` (every file: old and new name, local time,
UTC offset, confidence, evidence, rejected dates) and `review.tsv` (assumed and
weak dates grouped by folder, the place to decide on folder rules).

How the time is chosen, most reliable first:

| Confidence | Evidence |
| --- | --- |
| exact | local time with its offset (iPhone photos and videos); a local time whose offset the GPS clock in the file proves; a UTC time in the zone at the file's GPS position (needs the optional `timezonefinder` package) |
| assumed | a zoneless time read in the folder's zone or `Africa/Casablanca`; a UTC video time converted the same way; a full date and time in the file name |
| weak | a day from the file name (WhatsApp) or a dated folder (`2022-OCT-01`, `2015-09-06_TO_…`); the file's modified time |

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

`scripts/import-hdd-500-11.sh` copied the HDD_500_11 archive from the laptop.
It refuses any disk but HDD_500_11 (by filesystem UUID), is resumable, pauses
when lab1's CPU runs hot, checksums every file as it lands, and ends with a full
xxh128 comparison of both sides. Symlinks and special files stop the run instead
of being skipped. The end of every copy or verification is posted to Discord
through `DISCORD_WEBHOOK_ALERTS` in `compose/.env`:

```bash
photos/scripts/import-hdd-500-11.sh --dry-run    # plan only
photos/scripts/import-hdd-500-11.sh              # copy, then verify
photos/scripts/import-hdd-500-11.sh --verify-only
```

Keep the source drive untouched until the copy is verified and offsite backup of
`photos/` exists.
