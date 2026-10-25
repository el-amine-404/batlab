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
| Dangerzone CDR | BLOCKED | Debian 13 is supported, but Lab2 currently has neither Podman nor Dangerzone/gVisor. A fingerprint-verifying installer and fail-closed conversion probe are tracked; host installation still requires interactive sudo. Ordinary Docker is explicitly rejected as sufficient proof. |
| Exposed Paperless token | VERIFIED | Archived prototype token matched the live token. It was replaced in Paperless and `compose/.env`; Homepage and Paperless GPT were recreated; an authenticated API request using the new token returned HTTP 200. No token value was logged. |
| M1 | VERIFIED | The disposable production-shaped stack passed authentication/RBAC, streaming quarantine, idempotency, ownership, restart, audit, and publisher-confirmed outbox tests. Release remains impossible until CDR capability is verified. |

Commands used:

```text
find /home/bro/Documents -type f -iname '*.pdf' -printf '%s\n'
ssh lab2 'nproc; free -h; df -h / /mnt/storage/data; stat -fc %T /sys/fs/cgroup'
ssh lab2 'command -v dangerzone-cli podman runsc'
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

