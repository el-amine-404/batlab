# Immich

This stack follows Immich's official four-service architecture: server,
machine learning, Valkey, and PostgreSQL.

## Storage layout

```text
${DATA_ROOT}/immich/              Immich-managed originals and generated media
${VOLUMES_ROOT}/immich/
├── postgres/                    PostgreSQL database (keep on local SSD)
├── redis/                       Valkey working data
└── model-cache/                 downloaded machine-learning models

${IMMICH_EXTERNAL_LIBRARY}/      optional existing family archive (read-only)
```

`IMMICH_UPLOAD_LOCATION` is valuable data and must be backed up together with a
consistent PostgreSQL dump. The model cache and Redis data are reproducible.
The external library remains ordinary files and is never managed by Immich.

## Prepare and start

Copy the new Immich variables from `compose/.env.example` into the server's
untracked `compose/.env` and generate an alphanumeric database password. The
normal Batlab setup command creates every required bind-mount directory before
Docker starts:

```bash
make setup
make up STACK=immich
```

Open `http://immich.homelab.lan` and create the first administrator account.

## Add the future family archive

Mount the new data disk at a stable host path, then set, for example:

```dotenv
IMMICH_EXTERNAL_LIBRARY=/mnt/photos/family-archive
```

Verify that the directory exists and start Immich with the optional overlay:

```bash
test -d /mnt/photos/family-archive
docker compose --env-file compose/.env \
  -f compose/immich/docker-compose.yml \
  -f compose/immich/docker-compose.library.yml up -d
```

In Immich, create an external library with `/external/library` as its import
path. Keep the mount read-only so Immich cannot rename, move, or delete the
canonical archive. If the disk is removable, disable library watching and
periodic scanning before disconnecting it.

Do not enable the overlay until the storage exists. `create_host_path: false`
deliberately prevents Docker from creating a misleading root-owned directory
when the disk is absent or mounted at the wrong path.

## Check interrupted uploads

Use the read-only duplicate checker before recovering or removing files left in
Immich's upload staging directory:

```bash
compose/immich/scripts/check-upload-duplicates.sh
```

The script builds one temporary index of library file names and sizes, then uses
`cmp` only on same-sized candidates. This avoids repeatedly scanning the photo
disk and is faster than hashing the complete library while still proving
whether two files are byte-for-byte identical. It prints elapsed time while
indexing and approximate byte progress during large comparisons, so the user is
never left waiting without visible activity. Custom upload and library paths
can be supplied as arguments or named options; run it with `--help` for details.

## Rename an independent photo archive

`rename-media-by-creation-time.py` creates names such as
`2017-11-18_21h-49m-59s.jpg` from embedded creation metadata. Same-second
collisions receive `_02`, `_03`, and so on. Apple HEIC/JPG/MOV companions with
the same media-group identifier receive one shared capture-time stem, even when
the motion video starts a few seconds before the still image.

The default command only creates a durable plan; it does not rename media:

```bash
compose/immich/scripts/rename-media-by-creation-time.py plan \
  --sample 25 \
  /mnt/storage/data/media/photos/amine/iphone-import-2026-09-01/originals
```

Sample mode selects a deterministic, extension-aware subset and deliberately
does not generate an apply or rollback command. After reviewing the sample,
create the full actionable plan:

```bash
compose/immich/scripts/rename-media-by-creation-time.py plan \
  /mnt/storage/data/media/photos/amine/iphone-import-2026-09-01/originals
```

The printed state directory contains:

- `plan.tsv`: a reviewable old-name to new-name mapping and timestamp source;
- `manifest.json` and `manifest.sha256`: the immutable recovery record;
- `apply.sh`: a typed-confirmation, two-phase, no-overwrite rename;
- `rollback.sh`: a self-contained route back to every original filename;
- `tool.py`: the exact tool version that created the plan.

Review `plan.tsv`, then run the generated `apply.sh`. Use its neighboring
`rollback.sh` to restore the old names, or `tool.py status STATE_DIRECTORY` to
reconcile every planned inode after an interruption. Applying and rolling back
change directory entries only; they never rewrite media or embedded metadata.

Planning prefers original capture metadata and Apple `CreationDate`, then the
existing timestamp prefix. Files without a trustworthy time are skipped, and
apply refuses a plan containing skips unless they are explicitly accepted.
Filesystem modification time is used only with `--allow-mtime-fallback`.

Only the selected directory is scanned by default. Add `--recursive` for nested
directories. The tool deliberately refuses paths inside Immich's managed
`immich/library` and `immich/upload` trees.
