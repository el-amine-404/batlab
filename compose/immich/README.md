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

Naming the archive by capture time, including time zones, fake dates and
reversible XMP sidecars, is handled by `photos/scripts/organize-media.py`;
see `photos/README.md`. Run it before Immich indexes the archive, since a
rename later looks to Immich like a deletion followed by a new asset.
