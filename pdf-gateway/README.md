# PDF Security Gateway

This directory is the clean implementation of the v2 specification. It is not
the archived prototype. M1 provides the authenticated API, quarantine, database,
and durable job outbox; later milestones provide scanners and document release.
Capabilities must be promoted milestone by milestone in
`../docs/pdf-gateway/verification.md`.

Run the dependency-free contract checks with:

```bash
python3 pdf-gateway/tools/validate_contracts.py
```

Production deployment is intentionally absent until M1 passes its security gate.
The production-shaped Compose definition is now tracked, but it must not be
deployed as a usable document pipeline until the verified Dangerzone CDR provider
is available. No service may report a document as released before then.

Run the M1 disposable integration gate with:

```bash
pdf-gateway/tests/test_m1.sh
```

M2 adds the `scanning` profile. It keeps one ClamAV daemon with persistent
definitions and one low-concurrency analysis worker. The worker can read only
quarantine objects and can write only restricted scan reports. Start it only
after creating the host directories with ownership suitable for container UID
10002:

```bash
docker compose --profile scanning up -d
```

The production environment must provide `PDF_GATEWAY_STORAGE_ROOT` plus the M1
database, RabbitMQ, and bootstrap secrets. Validate the engines and durable task
path independently with:

```bash
pdf-gateway/tests/test_m2.sh
pdf-gateway/tests/test_m2_pipeline.sh
```

These tests use generated PDFs and EICAR, never files from the personal source
corpus. M2 still cannot release a document; `CDR_ELIGIBLE` transitions only to
`AWAITING_POLICY`.

## M3: signatures, CDR, and final validation

The `scanning` profile adds the signature worker; the `cdr` profile adds the
`cdr-worker` and `validation-worker`. A job moves through
`ANALYSIS → SIGNATURE → CDR → VALIDATION`. It is releasable only after the
validation worker passes the exact CDR output bytes. Database triggers (V004)
enforce this independently of the workers.

The CDR boundary is the host Dangerzone runtime, never a container. Install
the provider as the rootless Dangerzone account on Lab2:

```bash
hosts/lab2/pdf-cdr-provider/install.sh   # installs files only; does not enable
```

The provider listens on a Unix socket that is bind-mounted into `cdr-worker`
(`PDF_GATEWAY_CDR_SOCKET_DIR`). It accepts only the configured worker UID
(10004). Staged CDR output is mode 0640, group 10010, readable only by the
validation worker.

Lab2 verification (needs Dangerzone, so not runnable on other hosts):

```bash
pdf-gateway/tests/test_m3_cdr.sh          # provider, isolation evidence, typed failures
pdf-gateway/tests/test_m3_validation.sh   # final-byte validation and release guard
pdf-gateway/tests/test_pipeline_e2e.sh    # production compose end to end via the API, into the archive
```

Lab2's default Docker bridge has no DNS, so these tests build images with
`docker build --network host` and run Compose with `--no-build`. Nothing here is
deployed as a service; enabling the provider and the `cdr` profile is a separate
deployment decision after the M3 gate.

## M4: portable archive

The `archive` profile adds `archive-worker`, the only writer of
`PDF_GATEWAY_ARCHIVE_ROOT` (`archive/`, `staging/`, `manifests/` on one
filesystem). The API mounts the archive read-only. Released documents land in
`archive/<Category>/<year>/<Folder>/<date> - <issuer> - <title> [doc-…]/` with the
PDF, `metadata.json` (authoritative) and `content.txt`. Filing follows reviewed
rules in `manifests/rules.json` (see `contracts/rules.schema.json`); anything
uncertain goes to `Unsorted` for review. The archive works without PostgreSQL
or Paperless:

```bash
pdf-archive --root ARCHIVE verify | reindex --catalog c.sqlite | repair [--apply] | export --bundle b.tar | import --bundle b.tar | reindex-db
```

`pdf-archive` is the archive-worker image's entrypoint. Local tests:
`test_m4_archive.sh`, `test_m4_organize.sh`, `test_m4_worker.sh`,
`test_m4_metadata.sh`, `test_m4_gate.sh`.

Lab2-only tests must go through `pdf-gateway/tools/lab2_test.sh <test>`. It
builds images locally, samples temperature every second, and the tests abort
themselves at 85 °C. Lab2 runs live services.
