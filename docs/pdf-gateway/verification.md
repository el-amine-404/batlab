# Verification log

Capability status values are `IMPLEMENTED`, `VERIFIED`, `BLOCKED`, and
`NOT STARTED`. Commands are recorded without credentials or private content.

## 2026-10-25 — M0 baseline

| Capability | Status | Evidence |
|---|---|---|
| Unsafe prototype containment | VERIFIED | Prototype retained only on `archive/pdf-gateway-prototype-2026-10-25`; six containers removed; runtime data retained. |
| Real corpus inventory | VERIFIED | Read-only `find` counted 703 PDFs / 928,905,037 bytes under `/home/bro/Documents`; no files moved. |
| Lab2 baseline | VERIFIED | 8 CPUs, 31 GiB RAM, cgroup v2, 36 GiB root free, 1.9 TiB data free. |
| Portable archive contracts | IMPLEMENTED | Strict metadata/taxonomy schemas and OpenAPI draft are tracked and syntax-tested. |
| Threat model | IMPLEMENTED | Trust boundaries, abuse cases, fail-closed decisions, and residual risks are documented. |
| Dangerzone CDR | VERIFIED | Lab2 installed Dangerzone 0.11.1 and rootless Podman 5.4.2 with outer crun. The signed image digest `b1e2ad0aa19c5b994c5c8e49d74d9c4ee99fba1795cf7162ac7aa103440b9f97` verified against the packaged public key. A real conversion returned `CDR_READY` and output SHA-256 `b30f4e27459b601bf135e0d5927be06d4d8b50043356bb5cd246a7a5ef05fd66`; debug evidence showed inner gVisor/runsc, `--network=none`, and no host bind/volume mount. |
| Exposed Paperless token | VERIFIED | Archived prototype token matched the live token. It was replaced in Paperless and `compose/.env`; Homepage and Paperless GPT were recreated; an authenticated API request using the new token returned HTTP 200. No token value was logged. |
| M1 | VERIFIED | The disposable production-shaped stack passed authentication/RBAC, streaming quarantine, idempotency, ownership, restart, audit, and publisher-confirmed outbox tests. Release remains impossible until CDR capability is verified. |

Commands used:

```text
find /home/bro/Documents -type f -iname '*.pdf' -printf '%s\n'
ssh lab2 'nproc; free -h; df -h / /mnt/storage/data; stat -fc %T /sys/fs/cgroup'
ssh lab2 'command -v dangerzone-cli podman runsc'
ssh lab2 'cd /home/potato/batlab && pdf-gateway/tools/verify_cdr_runtime.sh'
python3 pdf-gateway/tools/validate_contracts.py
pdf-gateway/tests/test_m1.sh
docker run --rm ... maven:3.9.11-eclipse-temurin-21 mvn -B test
```

## 2026-10-25 — M1 gate

| Capability | Status | Evidence |
|---|---|---|
| Core runtime | VERIFIED | Digest-pinned PostgreSQL, RabbitMQ, build JDK, and runtime JRE; private backend network; API bound to loopback; non-root/read-only/capability-dropped API container. `docker compose config` accepts the production definition with injected placeholders. |
| Database | VERIFIED | Flyway created users, hashed tokens, jobs, steps, artifacts, idempotency keys, audit, and outbox structures on a fresh PostgreSQL 17 instance. |
| Authentication and authorization | VERIFIED | Missing token returned 401; hashed ADMIN token uploaded; USER could not enumerate another owner's scan (404); READ_ONLY upload returned 403. Spring's generated development account is disabled. |
| Quarantine | VERIFIED | A real generated PDF was streamed to an opaque generated object, hashed during transfer, and atomically renamed. A hostile display filename did not become a storage path; no raw quarantine HTTP route exists. |
| Idempotency | VERIFIED | Same owner/key/content returned the original scan and left exactly one object; same key with different bytes returned 409 and left exactly one object. |
| Durability | VERIFIED | API restart preserved the queued job. The transaction emitted one audit event and one outbox record; the record was marked published only after a correlated RabbitMQ broker acknowledgement. |
| Unit and contract checks | VERIFIED | Two Java unit tests passed; strict JSON/OpenAPI contract validator passed; `git diff --check` passed. |

The source corpus remained untouched. The test PDF and all databases/queues were
created in disposable storage and removed after the test. The production Compose
definition is tracked but is not deployed: without the M0 CDR capability it must
not imply a releasable document pipeline.

Unchecked risks: the CDR package/signing-key installation and isolation test require an explicit
host installation step; no source document has entered the new pipeline.

## 2026-10-25 — M2 scanner and durable-worker gate

| Capability | Status | Evidence |
|---|---|---|
| Live malware scanning | VERIFIED | ClamAV 1.4.6 served database 28164 dated 2026-10-25. The test forces daemon reload after FreshClam and rejects definitions older than 48 hours. A bounded `INSTREAM` scan detected the EICAR test signature. |
| Structural analysis | VERIFIED | Real qpdf 12.2.0, YARA 4.5.2, PDFiD 0.2.10, and pdf-parser 0.7.14 ran against generated clean, active-action, and malformed PDFs. Didier Stevens tools are pinned to commit `7d625d0b05c4`. |
| Deterministic policy | VERIFIED | Clean produced `CLEAN/CDR_ELIGIBLE`; active JavaScript produced `SUSPICIOUS/CDR_ELIGIBLE`; EICAR produced `MALICIOUS/BLOCK`; malformed, stale definitions, and unavailable ClamAV produced `UNKNOWN/REVIEW_REQUIRED`. No analysis outcome can produce `READY`. |
| Durable processing | VERIFIED | A real persistent RabbitMQ message drove the constrained worker through PostgreSQL state changes and an atomic mode-0600 report. Source SHA-256 was checked at claim and commit. Replaying the same message created no second attempt or artifact. |
| Production shape | IMPLEMENTED | The `scanning` profile provides digest-pinned ClamAV with persistent definitions and a non-root, read-only, capability-dropped worker. It has one-message prefetch, bounded subprocesses, CPU/RAM/PID limits, no update network, read-only quarantine, and write access only to reports. |
| Extended safe hostile corpus | VERIFIED | qpdf generated password-encrypted and embedded-attachment PDFs. Both were held for review with typed evidence. A deliberately non-responsive ClamAV socket produced `UNKNOWN/REVIEW_REQUIRED` within the configured timeout. |
| M2 gate | VERIFIED | Real engines, safe fixtures, typed versioned reports, fail-closed dependency behavior, durable processing, and replay idempotency passed. Resource-exhaustion and mid-processing crash recovery remain cross-milestone M8 hardening work and are not represented as complete. |

Commands used:

```text
pdf-gateway/tests/test_m2.sh
pdf-gateway/tests/test_m2_pipeline.sh
python3 -m py_compile pdf-gateway/analysis-worker/scan.py pdf-gateway/analysis-worker/worker.py
docker compose -f pdf-gateway/compose.yml --profile scanning config --quiet
```

All fixtures and service state were disposable. `/home/bro/Documents` was not
mounted, read, moved, or modified by these tests.

## 2026-10-25 — M3 signature-evidence slice

| Capability | Status | Evidence |
|---|---|---|
| Offline signature inspection | VERIFIED | Hash-locked pyHanko 0.38.0 ran in a digest-pinned, non-root, read-only, networkless container with CPU/RAM/PID/file limits. Reports separately record byte integrity, cryptographic validity, configured-root trust, revocation uncertainty, document coverage, modification level, DocMDP result, algorithms, and bounded certificate evidence. |
| Real cryptographic fixtures | VERIFIED | A disposable CA and leaf certificate produced a real SHA-256 visible PDF signature. With the explicit root it returned `SIGNED_VALID_TRUSTED`; without it, `SIGNED_VALID_UNTRUSTED`. No OS trust fallback or certificate/revocation network fetching was allowed. |
| Tamper and malformed handling | VERIFIED | A same-length mutation inside the signed byte range returned `SIGNED_INVALID` or typed `UNKNOWN`, always `REVIEW_REQUIRED`. Malformed input returned typed `UNKNOWN/REVIEW_REQUIRED` with exit status 2. |
| Visible-mark distinction | VERIFIED | A PDF containing visible “Handwritten signature” text but no signature dictionary returned `UNSIGNED`; the report explicitly states that visible/handwritten marks are not cryptographic signatures. Authenticity is not claimed. |
| Source immutability | VERIFIED | SHA-256 of the real signed fixture was identical before and after repeated inspection. The inspector has only a read-only fixture mount and never writes the source. Unknown parse state conservatively requires restricted original retention. |
| Durable signature stage | NOT STARTED | The verified inspector is not yet connected to RabbitMQ/database orchestration. M3 as a whole remains incomplete. |

Command used:

```text
pdf-gateway/tests/test_m3_signatures.sh
```

Implementation commit: `2ce4e10`.

