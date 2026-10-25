# PDF security gateway implementation checklist

This checklist implements `PDF_SECURITY_GATEWAY_FINAL_SPEC_v2.md`. A gate is
complete only when its evidence is recorded in `verification.md`; checked boxes
mean verified behavior, not merely committed code.

## Global rules

- [x] Preserve `/home/bro/Documents` as read-only source material.
- [x] Keep the portable archive authoritative; PostgreSQL and Paperless are projections.
- [x] Treat all source bytes and derived text/metadata as hostile.
- [x] Never release an artifact after a failed, unknown, stale, or skipped required check.
- [x] Revalidate and rescan the exact final bytes after every PDF-producing step.
- [x] Keep secrets out of Git, logs, sidecars, browser storage, and API responses.
- [x] Rotate the Paperless token exposed by the discarded prototype history.

## M0 — specification, feasibility, and contracts

- [x] Inventory repository, Lab2 resources, existing services, and the real PDF corpus.
- [x] Record trust boundaries, threats, mitigations, and residual risk.
- [x] Define portable archive metadata and taxonomy JSON Schemas.
- [x] Draft OpenAPI 3.1 external contract and typed problem responses.
- [x] Define the CDR adapter protocol and fail-closed capability check.
- [x] Install supported Dangerzone/Podman runtime on Lab2.
- [x] Verify signed sandbox image and gVisor-backed, networkless conversion on Lab2.
- [x] Run an isolation test proving no network and no host bind/volume access.
- [ ] Gate: review and accept the measured CDR approach.

## M1 — infrastructure, authentication, upload, and quarantine

- [x] Minimal core Compose profile with PostgreSQL and RabbitMQ on private networks.
- [x] Flyway migrations for users, jobs, steps, artifacts, audit, and outbox.
- [x] Secure bootstrap plus ADMIN, USER, and READ_ONLY authorization.
- [x] Streaming multipart upload with 50 MiB default limit and disk-headroom guard.
- [x] Private immutable quarantine with generated IDs and SHA-256 during streaming.
- [x] Transactional job/outbox creation and durable queue publication.
- [x] Restart, replay, idempotency, cross-user denial, and raw-file exposure tests.

## M2 — scanners and deterministic policy

- [x] Persistent ClamAV with freshness reporting and bounded INSTREAM scans.
- [x] qpdf/PDFiD/pdf-parser/YARA inspection in constrained workers.
- [x] Typed findings, exact engine/rule versions, and deterministic policy versions.
- [x] EICAR, malformed, encrypted, active-action, attachment, and timeout fixtures.
- [x] Prove scanner failures and stale signatures fail closed.
- [x] Durable single-concurrency worker, atomic restricted reports, and replay-safe acknowledgement.

## M3 — signatures, CDR, and final-byte verification

- [x] pyHanko signature evidence with immutable signed original handling.
- [x] Offline typed pyHanko inspection separates integrity, trust, revocation uncertainty, modification state, and visible non-cryptographic marks.
- [x] Durable signature queue, restricted report, signed-original state, transactional CDR outbox, and replay idempotency.
- [x] Verified Dangerzone adapter: host provider with per-conversion isolation evidence, bounded input/output/time, typed failures, and a durable cdr-worker stage.
- [ ] Optional bounded OCR and allowlisted metadata writer.
- [ ] Final qpdf/structure/YARA/ClamAV checks and exact-byte SHA-256.
- [ ] Visual regression and signed-original immutability tests.

## M4 — portable archive

- [ ] Deterministic taxonomy, evidence-backed organization, and safe path renderer.
- [ ] Same-filesystem staging, fsync, atomic directory publish, and recovery sweeper.
- [ ] Sidecar validation, immutable PDF versions, metadata CAS/ETag revisions.
- [ ] `verify`, `reindex`, `repair --dry-run`, `export`, `import`, and Paperless resync CLI.
- [ ] Recover a mixed archive with PostgreSQL and Paperless unavailable.

## M5 — mobile frontend/PWA

- [ ] Authenticated upload, jobs/SSE, findings, quarantine, archive, stats, and health.
- [ ] No raw-original preview or sensitive offline caching.
- [ ] CSP, CSRF, XSS, IDOR, desktop, and iPhone viewport tests.

## M6 — Paperless projection

- [ ] Server-only credential injection and documented Paperless API adapter.
- [ ] Durable retry, task reconciliation, deduplication, and metadata sync.
- [ ] Real outage/resume test proves archive independence and exactly-once projection.

## M7 — optional OCR and AI enrichment

- [ ] English/French/Arabic OCR with provenance and bounded resource use.
- [ ] Explicit private AI endpoint, authentication, schema validation, and redaction.
- [ ] Cached availability state machine, backoff/circuit breaker, and bounded queue.
- [ ] Offline/missing/busy/malformed/unauthorized/recovery tests.

## M8 — hardening, operations, and recovery

- [ ] Adversarial, fuzz, load, disk-full, crash, replay, and migration tests.
- [ ] Host-specific capacity budget and before/after critical-service measurements.
- [ ] Private metrics, structured redacted logs, actionable alerts, and runbooks.
- [ ] Encrypted backup and clean-host restore drill including archive-only recovery.
- [ ] Release checklist; every residual limitation is explicit.

