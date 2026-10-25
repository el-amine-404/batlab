# PDF gateway continuation ledger

This file is the resumable source of truth for agents continuing
`PDF_SECURITY_GATEWAY_FINAL_SPEC_v2.md`. Read the specification, this ledger,
`implementation-checklist.md`, and `verification.md` before changing code.

## Safety and workflow

- Preserve `/home/bro/Documents` read-only. Use generated disposable fixtures until the applicable gate passes.
- Never expose or release quarantine originals. Unknown, stale, skipped, timed-out, or failed security checks fail closed.
- Work in `/home/bro/.codex-worktrees/pdf-security-gateway` on `feature/pdf-security-gateway`.
- Keep milestones as small tested commits. Push the feature branch, merge verified slices into `main`, push, then pull `/home/potato/batlab` on Lab2.
- Do not send the final Discord completion notification until every M0-M8 definition-of-done gate passes.
- Another AI may edit unrelated files in the main worktree. Never discard unrelated dirty/untracked work; fetch and merge concurrent remote commits.

## Mandatory agent handoff protocol

Before starting a new milestone slice, check the active goal/usage budget. The
goal tool may retain a stale `usageLimited` result after the five-hour quota has
reset; do not treat that stale record as the sole authority if tool calls and
work are succeeding in the new session. Also assess the actual remaining
context and stop before a slice that cannot reasonably be implemented, tested,
documented, and committed.

Every agent that continues this project must update this file before stopping:

1. Record completed work with commit IDs and exact verification commands/results.
2. Record every uncommitted or partially implemented file and whether it is safe to keep.
3. Record the first exact command the next agent should run and any required user action.
4. Push the feature branch; merge only verified slices into `main`; pull `main` on Lab2.
5. Never claim a gate from another agent's note alone. The returning/next agent must inspect the diff and rerun the relevant tests before continuing.
6. If usage is low or limited, stop at the last green commit rather than beginning another change.

The latest green checkpoint is the source of truth; chat summaries are advisory.

## Verified state (2026-10-25 UTC)

- M0 contracts, architecture, threat model, schemas, and capacity plan are tracked.
- M1 is verified: authenticated streaming upload, RBAC, immutable opaque quarantine, disk headroom, PostgreSQL, RabbitMQ transactional outbox, audit, idempotency, ownership, restart durability.
- M2 is verified: live ClamAV definitions and INSTREAM, qpdf, pinned PDFiD/pdf-parser, YARA, typed deterministic outcomes, atomic restricted reports, one-at-a-time durable worker, replay idempotency. Safe fixtures cover clean, active action, EICAR, malformed, encrypted, attachment, stale/unavailable/timed-out ClamAV.
- Commands that passed locally:
  - `pdf-gateway/tests/test_m1.sh`
  - `pdf-gateway/tests/test_m2.sh`
  - `pdf-gateway/tests/test_m2_pipeline.sh`
- Main on Lab2 currently includes the durable M3 signature-stage checkpoint `d7d001c`.

## CDR gate — verified

Dangerzone 0.11.1 and its signed image digest
`sha256:b1e2ad0aa19c5b994c5c8e49d74d9c4ee99fba1795cf7162ac7aa103440b9f97`
are installed on Lab2. Signature verification passed. The first installer revision
incorrectly selected host `runsc` as Podman's outer runtime. Official Dangerzone
architecture uses rootless Podman/crun as the networkless outer container and
launches bundled gVisor/runsc inside it. Commit `e808050` corrects this.

The corrected installer was run on Lab2. This command passed:

```bash
ssh lab2 'cd /home/potato/batlab && pdf-gateway/tools/verify_cdr_runtime.sh'
```

It printed `CDR_READY dangerzone=0.11.1` with output SHA-256
`b30f4e27459b601bf135e0d5927be06d4d8b50043356bb5cd246a7a5ef05fd66`.
The verifier requires signed-image validation, outer crun, inner gVisor/runsc,
`--network=none`, no host bind/volume mounts, and a non-empty PDF output.

## Next implementation order

1. Continue M3 from durable signature-stage commit `f3efc26`. First inspect that commit and rerun `pdf-gateway/tests/test_m3_pipeline.sh`.
2. Implement the Lab2 host Dangerzone adapter for `CDR_QUEUED`, keeping rootless Podman/crun + inner gVisor, stdin-only source transfer, bounded output, timeouts and typed failure state.
3. Rescan the exact CDR output with ClamAV/qpdf/YARA before any release state; add safe graphical/handwritten appearance comparison without authenticity claims.
4. M4 portable archive and database-less reconstruction before any Paperless integration.
5. M5 real mobile frontend/PWA.
6. M6 exactly-once Paperless projection and outage recovery.
7. M7 optional OCR/AI with offline-safe behavior.
8. M8 hardening, measured Lab2 impact, monitoring, encrypted backup and clean restore.

### Latest green M3 slice

- Commit: `2ce4e10` (`feat(pdf-gateway): add offline signature evidence inspector`).
- Test: `pdf-gateway/tests/test_m3_signatures.sh` passed with real trusted,
  untrusted, tampered, visible-only and malformed fixtures.
- No partial or uncommitted implementation remains after this checkpoint.
- M3 is not complete and nothing from M3 has been deployed as a production service.

### Latest green durable-signature slice

- Commit: `f3efc26` (`feat(pdf-gateway): persist durable signature verification stage`).
- Tests passed: `test_m3_pipeline.sh`, `test_m3_signatures.sh`,
  `test_m2_pipeline.sh`, API Maven unit/Flyway build, production Compose config.
- No partial or uncommitted implementation remains after this checkpoint.
- The next exact work item is the host CDR adapter; do not mark a document
  released until the resulting exact bytes pass a new mandatory M3 rescan stage.

## M3 remaining plan (written 2026-10-25, before implementation)

Baseline rechecked first: `verify_cdr_runtime.sh` on Lab2 printed `CDR_READY`;
`test_m2.sh`, `test_m2_pipeline.sh`, `test_m3_signatures.sh` and
`test_m3_pipeline.sh` all passed locally.

Design decisions (safe, reversible):

- **Host CDR provider** (`hosts/lab2/pdf-cdr-provider/`): stdlib-only Python,
  systemd *user* service for the rootless Dangerzone account. It holds no
  database/broker credentials and cannot read quarantine. It accepts one job at a
  time over a Unix socket (SO_PEERCRED UID allowlist), receives length/SHA-256
  framed bytes (≤50 MiB), runs `dangerzone-cli --debug`, requires per-conversion
  evidence (signed image digest, `--network=none`, runsc, no bind/volume mount),
  bounds output size and time, kills exactly the named sandbox container on
  timeout, and returns typed `FAILED` codes. Dangerzone uses
  `--cgroups=disabled`, so the unit's CPU/memory/task limits contain it.
- **cdr-worker** container: consumes `pdf.gateway.cdr.v1`, re-derives
  eligibility from committed analysis + signature evidence (not from the message),
  rechecks the quarantine hash, streams to the provider, stores the output
  privately as `SANITIZED_STAGED`, records `cdr_results` lineage, emits
  `VALIDATION_QUEUED`.
- **validation-worker** (analysis image): exact-final-byte ClamAV + qpdf + YARA +
  decoded-structure postconditions (no actions/JS/forms/attachments/annotations),
  page-count and blank-page checks, hash recheck at start and commit. Pass →
  `FINAL_VALIDATED`/`ALLOW_SANITIZED`, status `AWAITING_ARCHIVE` with a durable
  `ARCHIVE_QUEUED` message held for M4. Anything else → `REVIEW_REQUIRED`/`BLOCKED`.
- **Database guard**: a trigger rejects any `AWAITING_ARCHIVE`/`ARCHIVING`/`READY`/`ALLOW_SANITIZED`/
  `FINAL_VALIDATED`/`PUBLISHED` state without a passing final validation of a
  CDR output whose hash matches. A direct SQL shortcut from `CLEAN` is tested.
- Optional OCR and allowlisted metadata writing are deferred (NOT STARTED). Any
  later PDF-producing step must create a new artifact and rerun final validation.

Slices, each tested on Lab2 with real engines, committed, merged:

1. Provider + cdr-worker + V003 `cdr_results`: unsigned, signed, active-content,
   CDR failure, timeout, provider unavailable, replay, source immutability.
2. Final validator + validation-worker + V004 guard: pass on real CDR output;
   EICAR/JavaScript-in-output, hash swap, ClamAV unavailable/stale, page-count
   mismatch and direct SQL bypass all fail closed.
3. End-to-end chain from the analysis queue: unsigned, validly signed, tampered,
   visible signature, handwritten/graphical appearance (rendered comparison),
   malformed, encrypted, active content.
4. Public contract alignment: the API currently returns internal values
   (`CLEAN`, `CDR_ELIGIBLE`, `AWAITING_CDR`, `SIGNED_ORIGINAL_RESTRICTED`) that
   the OpenAPI enums do not allow; fix with a migration and mapping.

Never skip a milestone gate or substitute mocked security verdicts for actual
engine execution.
