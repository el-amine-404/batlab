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

## 2026-10-25 — M3 durable signature-stage slice

| Capability | Status | Evidence |
|---|---|---|
| Durable orchestration | VERIFIED | Flyway V002 stores versioned signature verification reports. Analysis completion emits `SIGNATURE_QUEUED` through the existing transactional outbox. A durable single-prefetch worker rechecks the quarantine SHA-256 before inspection and again before commit. |
| Signed-original policy | VERIFIED | A trusted signed fixture advanced to `AWAITING_CDR` with `SIGNED_ORIGINAL_RESTRICTED`; its restricted signature report and typed database record were committed before broker acknowledgement. Original SHA-256 remained unchanged. |
| Fail-closed tamper policy | VERIFIED | A mutated signed source stopped at `REVIEW_REQUIRED` and emitted no `CDR_QUEUED` event. It could not enter the sanitization path. |
| Replay idempotency | VERIFIED | Replaying the signed source's durable broker message left exactly one verification record and one signature job step. |
| Production constraints | IMPLEMENTED | The `scanning` profile includes a non-root/read-only/capability-dropped signature worker with no external network, bounded resources, read-only quarantine and trust store, and write access only to signature reports. Trust roots must be explicit; empty configuration yields untrusted rather than OS trust fallback. |
| Regression checks | VERIFIED | API/Flyway unit build passed (2 tests); the M2 durable analysis pipeline passed after adding its signature outbox transition; standalone M3 signature tests passed after the dependency/inspector changes. |

Commands used:

```text
pdf-gateway/tests/test_m3_pipeline.sh
pdf-gateway/tests/test_m3_signatures.sh
pdf-gateway/tests/test_m2_pipeline.sh
docker run --rm -v "$PWD/pdf-gateway/api:/src" -w /src maven:3.9.11-eclipse-temurin-21@sha256:6fdc855a6ed81d288ca7ca37ac6ff5e9308b612485c0801d70b25a858c83d237 mvn -B test
docker compose -f pdf-gateway/compose.yml --profile scanning config --quiet
```

Implementation commit: `f3efc26`. M3 remains incomplete until the isolated CDR
adapter, exact-final-byte rescan, and visual preservation gates pass.


## 2026-10-25 — M3 slice 1: host CDR provider and durable CDR stage

| Capability | Status | Evidence |
|---|---|---|
| Baseline recheck | VERIFIED | Before changes, `verify_cdr_runtime.sh` on Lab2 printed `CDR_READY dangerzone=0.11.1`; `test_m2.sh`, `test_m2_pipeline.sh`, `test_m3_signatures.sh`, `test_m3_pipeline.sh` passed. |
| Host CDR provider | VERIFIED | `hosts/lab2/pdf-cdr-provider/provider.py` (stdlib only) ran as a transient systemd user unit for the rootless account with the tracked unit's limits. It holds no database/broker credentials and has no quarantine access; bytes arrive over a Unix socket with a length and SHA-256 header. A host-account connection was rejected with `PEER_NOT_ALLOWED` (SO_PEERCRED allowlist). |
| Per-conversion isolation evidence | VERIFIED | Each success requires Dangerzone's own launch command to use the verified image digest, `--network=none`, `--cap-drop all`, `no-new-privileges`, `--userns nomap`, user `dangerzone`, no host bind/volume mount, plus an inner `runsc --network=none` launch. Otherwise output is discarded as `ISOLATION_EVIDENCE_MISSING`. Stored evidence: image `sha256:b1e2ad0aa19c…9f97`, inner/outer network `none`, host mounts `none`. |
| Resource containment | VERIFIED | During a 250-page conversion, `runsc` and `gvisor_sentry` were observed inside the provider unit's cgroup with `memory.max` = 3 GiB. Dangerzone's `--cgroups=disabled` keeps the sandbox in the caller's cgroup. Finding: `RLIMIT_FSIZE` on the CLI crashes gVisor's sparse memory file, so host files are bounded by polling instead. |
| Durable CDR stage | VERIFIED | `cdr-worker` (UID 10004, read-only, no capabilities, 256 MiB, no external network) re-derives eligibility from committed `ANALYSIS` and signature evidence for the exact source hash, rechecks the quarantine hash, stages output at mode 0600, and records `cdr_results` lineage (provider/Dangerzone versions, image digest, isolation evidence, duration, output hash). It emits `VALIDATION_QUEUED`; status becomes `VALIDATING_OUTPUT`, never releasable. |
| Real conversions | VERIFIED | Unsigned, active JavaScript/OpenAction, and pyHanko-signed fixtures converted in about 4.8 s each. Output SHA-256 differed from the source and matched the artifact row; quarantine sources were unchanged. |
| Typed CDR failures | VERIFIED | Malformed input: `CONVERSION_FAILED` → `REVIEW_REQUIRED`, no artifact, no outbox event. Missing signature evidence: `REVIEW_REQUIRED` without a conversion. 5 s provider timeout on 250 pages: `TIMEOUT`; the named sandbox container was killed and none remained. Provider stopped: `PROVIDER_UNAVAILABLE` (retryable); job back to `AWAITING_CDR` with a delayed durable `CDR_QUEUED` retry. |
| Replay idempotency | VERIFIED | Replaying a completed job's message created no second result or artifact. |
| Signed-original retention | VERIFIED | Migration V003 moves the signed-original flag from `artifact_state` into `original_retention`, so staging a sanitized copy cannot erase it. A trigger rejected an attempt to lower `SIGNED_RESTRICTED`. |
| Regression | VERIFIED | `test_m3_pipeline.sh` (updated retention assertion), `test_m1.sh` (V003 applied through the API's Flyway), and the Maven build passed. |

Commands used:

```text
ssh lab2 'cd /home/potato/batlab && pdf-gateway/tools/verify_cdr_runtime.sh'
ssh lab2 'cd <checkout> && pdf-gateway/tests/test_m3_cdr.sh'      # Lab2 only: needs Dangerzone
pdf-gateway/tests/test_m3_pipeline.sh
pdf-gateway/tests/test_m1.sh
docker run --rm -v "$PWD/pdf-gateway/api:/src" -w /src maven:3.9.11-eclipse-temurin-21@sha256:6fdc855a… mvn -B -q test
```

Limits: this stage test seeds the committed M2/signature evidence rows directly,
to isolate the CDR stage. The end-to-end chain through the real analysis and
signature engines is slice 3. The provider is installed only as files; it is not
enabled as a persistent service, and Lab2 linger is off.

## 2026-10-25 — M3 slice 2: exact-final-byte validation and release guard

| Capability | Status | Evidence |
|---|---|---|
| Final validator | VERIFIED | `final_validate.py` (policy `final-policy-1`) runs on the exact staged bytes: hash equals the CDR record at start and end; ClamAV `INSTREAM` must be `CLEAN` with definitions ≤48 h old; qpdf check; not encrypted; zero raw PDF security keywords; zero YARA matches; decoded qpdf JSON object graph has no actions/JavaScript/forms/annotations/attachments/fonts/names/non-image XObjects; page resources are images only; output page count equals the source. Any finding denies release; only an empty failure list yields `ALLOW_SANITIZED`. |
| Visual checks | VERIFIED | The sanitized output (never the original) is rendered at 12 DPI with poppler. An all-white output gave `OUTPUT_ALL_PAGES_BLANK` → `REVIEW_REQUIRED`. Individual blank pages are recorded as INFO. This flags blank/missing pages; it does not certify fidelity. |
| Real CDR output passes | VERIFIED | Unsigned, 3-page and handwritten/graphical fixtures went through the real provider → Dangerzone → cdr-worker → validation-worker. Each reached `AWAITING_ARCHIVE`, `ALLOW_SANITIZED`, `FINAL_VALIDATED`, `NO_THREATS_DETECTED`. Each has a `PASS` record with ClamAV/qpdf/YARA/poppler versions, a restricted report artifact, and one durable `ARCHIVE_QUEUED` event (queue declared, consumer arrives in M4). |
| Final-rescan failures | VERIFIED | Seeded "CDR outputs" a compromised converter could emit. EICAR in a stream: `MALWARE_DETECTED` → `BLOCKED`. JavaScript/OpenAction: keywords, YARA, `FORBIDDEN_STRUCTURE` → `REVIEW_REQUIRED`. Wrong page count: `PAGE_COUNT_MISMATCH`. Bytes changed after staging: `OUTPUT_HASH_MISMATCH`. Blank output: `OUTPUT_ALL_PAGES_BLANK`. Each artifact was set `REJECTED`, with no archive event. |
| Scanner unavailable | VERIFIED | With ClamAV stopped, a real Dangerzone output got `UNKNOWN`/`CLAMAV_UNAVAILABLE`. It stayed `VALIDATING_OUTPUT`/`SANITIZED_STAGED` with a delayed durable retry; no release. An unreadable output is likewise a typed `UNKNOWN` (`OUTPUT_UNREADABLE`), never an exception loop. |
| Database release guard | VERIFIED | Migration V004 triggers rejected every attempt: an initial-`CLEAN` job set to `READY`, `AWAITING_ARCHIVE`, `ALLOW_SANITIZED` or `PUBLISHED`; inserting a job directly as `READY`; promoting a rejected artifact; forging a `PASS` for a non-matching artifact; editing a validation record (append-only); changing a CDR artifact's hash or storage path. The release query returned exactly the four passing jobs. |
| Staged-output permissions | VERIFIED | CDR output is 0640, owner UID 10004, group 10010. Only the validation worker (`group_add: 10010`) can read it; the host account cannot. Found by the test: with 0600 files, validation failed closed and could not proceed. |
| Replay | VERIFIED | Republishing a validated job's message created no second validation record. |

Command used:

```text
ssh lab2 'cd <checkout> && pdf-gateway/tests/test_m3_validation.sh'   # Lab2 only
```

Limits: final-failure outputs were seeded directly as CDR results (the real
converter does not emit them). Real-converter outputs were validated end to end
within this stage. Page boxes are not compared with the source, to avoid parsing
the hostile original further; page count is.

## 2026-10-25 — M3 slice 3: end-to-end signature-aware CDR on the production definition

`test_m3_e2e.sh` ran the production `pdf-gateway/compose.yml` (`scanning` and
`cdr` profiles) on Lab2. A test override only substitutes a disposable trust
root. The host provider ran as a transient user unit with the tracked limits.
Ten generated fixtures were uploaded through the authenticated API with
idempotency keys. The real outbox publisher drove every stage.

| Fixture | Outcome | Evidence |
|---|---|---|
| Unsigned text | VERIFIED released-to-archive | `AWAITING_ARCHIVE`, `ALLOW_SANITIZED`, `FINAL_VALIDATED`, final `PASS`, `ARCHIVE_QUEUED` published. |
| Validly signed (visible pyHanko signature, trusted test root) | VERIFIED | `SIGNED_VALID_TRUSTED`; `original_retention=SIGNED_RESTRICTED`; viewing copy passed final validation. pyHanko on the viewing copy: `UNSIGNED`, 0 signatures (a new unsigned PDF, not the signed document). Signature-widget ink 0.142 → 0.083, retained. |
| Tampered after signing (byte flip in signed range) | VERIFIED held | `SIGNED_INVALID` → `REVIEW_REQUIRED`; no CDR attempted; retention `SIGNED_RESTRICTED`. |
| Modified after signing (incremental content update) | VERIFIED held | Bytes intact, modification level `OTHER`, DocMDP not permitted → `SIGNED_MODIFIED` → `REVIEW_REQUIRED`; no CDR. |
| Visible signature-like text, no signature | VERIFIED | `UNSIGNED`, retention `STANDARD`, released. Not treated as cryptographic. |
| Handwritten/graphical marks (vector strokes + raster image) | VERIFIED | `UNSIGNED`, released. Region ink: vector 0.061 → 0.073, raster 0.170 → 0.172. No authenticity claimed. |
| Malformed | VERIFIED held | Analysis `UNKNOWN` → `REVIEW_REQUIRED`; no signature stage, no CDR. |
| Encrypted (AES-256, qpdf) | VERIFIED held | `PDF_ENCRYPTED` → `REVIEW_REQUIRED`; no CDR. |
| Active JavaScript/OpenAction | VERIFIED | Analysis `SUSPICIOUS`/`CDR_ELIGIBLE`; rasterised; the final validator found no active content; released with `NO_THREATS_DETECTED` for the new bytes. |
| EICAR in a PDF stream | VERIFIED blocked | ClamAV match at analysis → `MALICIOUS`/`BLOCK`/`BLOCKED`; no later stage. |

Cross-cutting evidence:

- Exactly five jobs were in any release-eligible state; the other five never reached CDR.
- Every quarantined original's SHA-256 equalled the uploaded fixture after the
  whole pipeline. The stored `source_sha256` matched.
- Neither `/api/v1/quarantine/{id}` nor `/api/v1/artifacts/{id}/download`
  returned a 2xx response. No route serves original or staged bytes.
- Regressions passed afterwards: `test_m1.sh`, `test_m2.sh`,
  `test_m2_pipeline.sh`, `test_m3_pipeline.sh`, `test_m3_signatures.sh`,
  `validate_contracts.py`, and `test_m3_cdr.sh` on Lab2.

Command used:

```text
ssh lab2 'cd <checkout> && pdf-gateway/tests/test_m3_e2e.sh'   # Lab2 only, about 4.5 min
```

Limits: appearance checks compare ink inside known regions at 50 DPI, a gross
preservation check rather than visual fidelity. The tested outputs are small
generated documents. Representative real-document latency, memory and quality
measurements belong to M8. Detection vocabulary is not yet uniform
(`CLEAN` from M2 vs `NO_THREATS_DETECTED` after validation); slice 4 aligns
the public contract.

## 2026-10-25 — M3 slice 4: public scan contract alignment

| Capability | Status | Evidence |
|---|---|---|
| Contract drift found | VERIFIED | `GET /api/v1/scans/{id}` returned raw database values outside the OpenAPI `Scan` enums (`AWAITING_CDR`, `AWAITING_ARCHIVE`, `CLEAN`, `CDR_ELIGIBLE`) and undeclared fields (`sourceBytes`, `sourceSha256`) under `additionalProperties: false`. A pre-change response shape failed the new checker. |
| Boundary mapping | VERIFIED | `ScanContract` maps internal values to contract values (`AWAITING_CDR`→`SANITIZING`, `AWAITING_ARCHIVE`→`ARCHIVING`, `CLEAN`→`NO_THREATS_DETECTED`). The exact internal state is exposed as the diagnostic `stage`. Unknown or null values map to `REVIEW_REQUIRED`/`UNKNOWN`/`ORIGINAL_QUARANTINED`/`SIGNED_RESTRICTED`, never to a release state (`ScanContractTest`, 2 tests). |
| OpenAPI 0.2.0 | VERIFIED | The `Scan` schema adds `stage`, `originalRetention`, `sourceBytes`, `sourceSha256`, and policy `CDR_ELIGIBLE` (spec §5 step 5). `validate_contracts.py` passed. No database migration was needed. |
| Live responses | VERIFIED | `test_m3_e2e.sh` checked all ten live scan views against the `Scan` schema in `openapi.yaml` (exact keys, enums, patterns). Signed: `ARCHIVING`/`AWAITING_ARCHIVE`/`ALLOW_SANITIZED`/`FINAL_VALIDATED`/`SIGNED_RESTRICTED`. Modified-after-signing: `REVIEW_REQUIRED`, `SIGNED_RESTRICTED`. EICAR: `BLOCKED`/`MALICIOUS`/`BLOCK`. |
| Regression | VERIFIED | Maven (4 tests), `test_m1.sh`; from Lab2 main `fc52569`: `test_m3_validation.sh`, `test_m3_e2e.sh`. |

## M3 gate summary (2026-10-25)

| Gate item | Status |
|---|---|
| pyHanko signature inspection with typed evidence; cryptographic vs visible/handwritten marks | VERIFIED |
| Signed originals immutable and retention-restricted; viewing copy unsigned | VERIFIED |
| Isolated CDR through the verified Dangerzone boundary, with per-conversion evidence | VERIFIED |
| Exact-final-byte qpdf/YARA/ClamAV/structure revalidation; no CLEAN→release shortcut (database-enforced) | VERIFIED |
| Image, graphic (vector) and handwritten/signature appearance preserved; blank/missing pages flagged | VERIFIED (gross ink-region checks, not fidelity) |
| Unsafe/unknown output never released | VERIFIED |
| Optional bounded OCR and allowlisted metadata writer | NOT STARTED (optional, deferred to M4/M7) |
| Persistent provider service and `cdr` profile deployment | NOT STARTED (deliberately not deployed) |
| M0 item "review and accept the measured CDR approach" | BLOCKED on user acceptance |

## 2026-10-25 — M0 CDR gate accepted; Lab2 thermal incident

| Item | Status | Evidence |
|---|---|---|
| CDR approach | VERIFIED (accepted) | The user accepted the measured approach: rootless Dangerzone on Lab2 with outer crun and inner gVisor, no network, no host mounts, final-byte revalidation. Stated residual risk: converter/gVisor/kernel bugs, deceptive visible content, and loss of text/vector fidelity. A dedicated VM was offered as the stronger alternative and declined. |
| Lab2 overheating during M3 tests | VERIFIED fixed | Netdata reported 95.5 °C during `test_m3_validation.sh`. Busiest: the test RabbitMQ at 172% CPU, test ClamAV at 63%, Frigate at 45%. The test PostgreSQL/RabbitMQ (and the production compose's) had no CPU limit. Every service in `compose.yml` and in each test compose file now has a CPU cap (PostgreSQL/RabbitMQ 0.5 CPU and 512 MiB; ClamAV 1.0, previously 1.5 production / 2.0 tests). RabbitMQ runs with Erlang scheduler busy-waiting disabled. A local idle and connection-churn comparison did not show that busy-waiting was the cause, so the hard cap is the actual safeguard. |
| Thermal guard | VERIFIED | Lab2 tests (`test_m3_cdr.sh`, `test_m3_validation.sh`, `test_m3_e2e.sh`) wait for `x86_pkg_temp` < 75 °C before starting and abort through their cleanup trap at ≥ 85 °C. Checked on Lab2: a normal run completed; a forced 1 °C threshold aborted with exit 143 and ran cleanup. Lab2 sat at 62–72 °C at rest. |

Regression after the caps (local): `test_m1.sh`, `test_m2.sh`, `test_m2_pipeline.sh`,
`test_m3_pipeline.sh`, `test_m3_signatures.sh` passed; all compose files pass
`docker compose config`. Not yet rerun on Lab2 after the cap change:
`test_m3_cdr.sh`, `test_m3_validation.sh`, `test_m3_e2e.sh`. They run once,
under the thermal guard, at the M4 integration step.
