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

## Importing a drive

`scripts/import-hdd-500-11.sh` copied the HDD_500_11 archive from the laptop.
It is resumable, pauses when lab1's CPU runs hot, checksums every file as it
lands, and ends with a full xxh128 comparison of both sides:

```bash
photos/scripts/import-hdd-500-11.sh --dry-run    # plan only
photos/scripts/import-hdd-500-11.sh              # copy, then verify
photos/scripts/import-hdd-500-11.sh --verify-only
```

Keep the source drive untouched until the copy is verified and offsite backup of
`photos/` exists.
