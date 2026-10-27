# PDF Security Gateway

This directory is the clean implementation of the v2 specification. It is not
the archived prototype. M1 provides the authenticated API, quarantine, database,
and durable job outbox; later milestones provide scanners and document release.
Capabilities must be promoted milestone by milestone in
`../docs/pdf-gateway/verification.md`.

Run the dependency-free contract checks with:

```bash
python3 compose/pdf-gateway/app/tools/validate_contracts.py
```

Production deployment is intentionally absent until M1 passes its security gate.
The production-shaped Compose definition is now tracked, but it must not be
deployed as a usable document pipeline until the verified Dangerzone CDR provider
is available. No service may report a document as released before then.

Run the M1 disposable integration gate with:

```bash
compose/pdf-gateway/app/tests/test_m1.sh
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
compose/pdf-gateway/app/tests/test_m2.sh
compose/pdf-gateway/app/tests/test_m2_pipeline.sh
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
compose/pdf-gateway/app/tests/test_m3_cdr.sh          # provider, isolation evidence, typed failures
compose/pdf-gateway/app/tests/test_m3_validation.sh   # final-byte validation and release guard
compose/pdf-gateway/app/tests/test_pipeline_e2e.sh    # production compose end to end via the API, into the archive
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

Lab2-only tests must go through `compose/pdf-gateway/app/tools/lab2_test.sh <test>`. It
builds images locally, samples temperature every second, and the tests abort
themselves at 85 °C. Lab2 runs live services.

## M5: web UI

`web/` is a React PWA served by the stateless `web` container (127.0.0.1:18088 by
default; put the homelab Caddy in front for HTTPS). It proxies `/api` same-origin.
People sign in with a name and a password (PBKDF2-hashed; five wrong tries in 15
minutes slow further tries down) and get an HttpOnly session cookie. Until an admin
can sign in, the page asks for the setup code the API writes to its log
(`docker logs pdf-gateway-api 2>&1 | grep 'setup code'`); an admin that only had a
token (the bootstrap `admin`) keeps its documents by taking its name there. Admins
then add people with a role (admin, user, read-only) and apps (Homepage, the agent,
scripts) with API keys, under System. The app never previews originals; approved
PDFs are downloads only.

```bash
compose/pdf-gateway/app/tests/test_m5_api.sh
VENV=… PLAYWRIGHT_BROWSERS_PATH=… compose/pdf-gateway/app/tests/test_m5_ui.sh [SCREENSHOT_DIR]
```

## Identity documents

Cards and passports file themselves from their machine-readable zone (`archive-worker/identity.py`),
kept only when its check digits agree: Identity › the holder, from the issuing state, with the
number and the expiry (`holder`, `validUntil` in the sidecar). `python3 tests/identity_test.py`
(from `archive-worker/`, with `PDF_ARCHIVE_SCHEMA=../contracts/archive-metadata.schema.json`).

## M7: optional OCR and AI (laptop agent)

Enrichment is off unless `PDF_GATEWAY_ENRICHMENT_ENABLED=true`. When on, documents
filed for review get an OCR job (no text layer) or an AI suggestion job. The agent
in `enrichment-agent/` runs on the AI laptop next to LM Studio and *pulls* work, so
the laptop exposes nothing and may be off at any time.

1. Under System → Apps and API keys, add `t1000-agent` with the AI agent role (its key
   is shown once). Or by SQL (store only the token's SHA-256):
   `INSERT INTO users(id,username,role) VALUES (gen_random_uuid(),'t1000-agent','ENRICHMENT');`
   `INSERT INTO api_tokens(id,user_id,token_hash,label) SELECT gen_random_uuid(),id,'<sha256 of token>','agent' FROM users WHERE username='t1000-agent';`
2. On the laptop: copy `agent.env.example` to `agent.env` (gateway URL, token, model), then
   `docker compose -f compose/pdf-gateway/app/enrichment-agent/compose.yml up -d --build`.
3. The agent loads `qwen/qwen3-4b-2507` in LM Studio at a 4096-token context, which
   fits a 4 GB GPU; OCR uses Tesseract (eng+fra+ara).

OCR text may re-file a document through the reviewed rules. AI output only becomes
proposals with quoted evidence, which a user applies or dismisses in the UI.
