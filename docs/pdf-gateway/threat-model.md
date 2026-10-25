# Threat model

## Assets and trust boundaries

The hostile boundary begins at the first uploaded byte. Quarantine originals,
filenames, PDF objects, metadata, extracted text, OCR, certificates, and AI output
are untrusted. The public API terminates authenticated HTTPS but cannot access
archive files by caller-provided paths. Workers receive opaque artifact IDs and
bounded streams. Only the archive publisher may write the archive; browser,
Filebrowser, SMB, Paperless, and indexers receive approved files read-only.

PostgreSQL is operational state, RabbitMQ transports identifiers, and Paperless
is a downstream projection. None is an archive authority. The archive PDF,
validated sidecar, taxonomy, and manifest must be sufficient for recovery.

## Threats and required controls

| Threat | Required control | Failure behavior |
|---|---|---|
| Parser/renderer exploit | Disposable networkless Dangerzone sandbox under gVisor; no shared host paths; bounded pixel channel | `FAILED`; retain quarantine; publish nothing |
| Known malware | Current ClamAV definitions, YARA, original and exact-final-byte scans | `BLOCK` or `UNKNOWN`; never release |
| Active PDF content | Bounded structure analysis; raster reconstruction; output postconditions forbidding actions, attachments, forms, and rich media | `REVIEW_REQUIRED` or `BLOCK` |
| PDF/decompression bomb | Streaming upload; byte/page/object/expanded-data/time/memory/pid/scratch limits | Abort worker; explicit resource-limit finding |
| Path traversal/overwrite | Opaque IDs; normalized allowlisted path renderer; same-filesystem atomic create; no caller paths | Reject request; audit event |
| IDOR/cross-user access | Object-level authorization on every artifact and event; random opaque IDs | RFC 9457 denial without existence leak |
| Stored XSS/prompt injection | Render strings as text; strict CSP; bounded schema-validated AI suggestions; AI cannot decide policy | Discard invalid suggestion |
| Queue replay/crash | Idempotency keys, durable messages, DB leases, transactional outbox, unique stage/artifact constraints | Resume or dead-letter visibly |
| Partial archive publish | Same-filesystem staging, fsync, schema/hash checks, atomic directory rename, reconciliation | Incomplete staging remains private |
| Sidecar/database drift | Sidecar revision plus ETag/CAS; append-only manifest events; verify/reindex/repair | Report drift; do not silently rewrite |
| Credential disclosure | Runtime secret injection, redaction, no secrets in sidecars or frontend | Disable integration and alert |
| Host resource starvation | One active heavy job per class; cgroup limits; low priority; disk/inode and PSI guards | Pause/defer with hysteresis |
| Direct Paperless bypass | Document and restrict all Paperless upload/consume/email routes | Never claim gateway coverage for bypassed input |

## CDR security decision

Ordinary Docker isolation is not accepted as the CDR security boundary. The
chosen adapter is Dangerzone on supported Debian, using its signed sandbox image,
Podman, network isolation, and gVisor defense in depth. The gateway passes source
bytes on standard input and accepts only the bounded page-size/RGB protocol.
It never bind-mounts quarantine into the sandbox and never grants a Docker socket.

The adapter reports `UNAVAILABLE` until capability tests prove all requirements.
M1 and M2 may quarantine and analyze while CDR is unavailable, but policy cannot
transition a job to `READY` or `PUBLISHED`.

Upstream basis:

- [Dangerzone security model](https://docs.dangerzone.rocks/latest/explanation/security-model/)
- [Dangerzone sandbox protocol](https://docs.dangerzone.rocks/dev/reference/sandbox-protocol/)
- [Dangerzone installation](https://docs.dangerzone.rocks/latest/how-to/install/)
- [Docker Engine security boundaries](https://docs.docker.com/engine/security/)

## Residual risk
Native-text extraction (M4) runs `pdftotext` on CDR-eligible originals inside
the networkless, read-only, capability-free analysis container under the same
CPU/memory/file rlimits as qpdf, limited to 20 pages and 256 KiB. That adds
poppler's text and font parsing to the surface already exposed to hostile
originals, without the gVisor sandbox. The text is untrusted data: it can select
only rule-defined categories, folders, issuers and taxonomy tags, plus a parsed
date, and is stored verbatim as `content.txt`. It never affects a security verdict
or a filesystem path. Hidden or invisible text in an original therefore appears in
`content.txt`.


Raster reconstruction reduces active-content risk; it does not prove safety.
Converter, image-decoder, kernel, gVisor, and host vulnerabilities remain possible.
The viewing copy can still contain deceptive visible content. ClamAV/YARA miss
unknown malware. OCR and classification can be wrong. Signature verification
describes cryptographic evidence and trust configuration, not signer identity or
truth of document content. These limitations must remain visible in the UI/API.

