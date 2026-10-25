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

## Verified state (2026-10-25 UTC, after M3)

- M0 contracts, architecture, threat model, schemas, and capacity plan are tracked.
  The M0 item "review and accept the measured CDR approach" still needs the user's acceptance.
- M1 VERIFIED: authenticated streaming upload, RBAC, immutable opaque quarantine,
  headroom guard, PostgreSQL, RabbitMQ transactional outbox, audit, idempotency,
  ownership, restart durability.
- M2 VERIFIED: live ClamAV, qpdf, pinned PDFiD/pdf-parser, YARA, typed fail-closed
  policy, durable single-concurrency worker.
- M3 VERIFIED (implementation gate; see the gate table at the end of
  `verification.md`). Optional OCR/metadata writer is NOT STARTED, and nothing from
  M3 is deployed as a service.

### M3 commits (all on `main`, Lab2 pulled)

| Commit | Content |
|---|---|
| `2ce4e10`, `f3efc26` | (earlier agent) pyHanko inspector; durable signature stage |
| `ee8b1e4` | Host Dangerzone provider (`hosts/lab2/pdf-cdr-provider/`), `cdr-worker`, V003 `cdr_results` + `original_retention` |
| `c160831` | Final validator + `validation-worker`, V004 `final_validations` + release-guard triggers, archive queue |
| `a8d3eef` | End-to-end test on the production compose via the API |
| `26924eb` | API/OpenAPI 0.2.0 scan contract alignment (`ScanContract`) |

### Pipeline as implemented

`QUEUED → SCANNING (analysis) → AWAITING_POLICY → ANALYZING (signature) →
AWAITING_CDR → SANITIZING (cdr-worker → host provider → Dangerzone) →
VALIDATING_OUTPUT (validation-worker) → AWAITING_ARCHIVE` + durable
`ARCHIVE_QUEUED` on `pdf.gateway.archive.v1`, which **has no consumer until M4**.
The public API maps internal values to the contract (`AWAITING_ARCHIVE`→`ARCHIVING`).
`READY`/`PUBLISHED` are unreachable without M4 and are trigger-guarded.

### Test commands and outcomes (all passed 2026-10-25)

Local (any Docker host): `pdf-gateway/tests/test_m1.sh`, `test_m2.sh`,
`test_m2_pipeline.sh`, `test_m3_signatures.sh`, `test_m3_pipeline.sh`,
`python3 pdf-gateway/tools/validate_contracts.py`, and Maven:
`docker run --rm -v "$PWD/pdf-gateway/api:/src" -w /src maven:3.9.11-eclipse-temurin-21@sha256:6fdc855a6ed81d288ca7ca37ac6ff5e9308b612485c0801d70b25a858c83d237 mvn -B -q test`
(4 tests; delete the root-owned `pdf-gateway/api/target` afterwards, it is not ignored).

Lab2 only (needs Dangerzone; run as `potato` in a checkout):
`pdf-gateway/tools/verify_cdr_runtime.sh` (`CDR_READY`), `pdf-gateway/tests/test_m3_cdr.sh`
(about 1.5 min), `test_m3_validation.sh` (about 2.5 min), `test_m3_e2e.sh` (about 4.5 min).
`test_m3_cdr`, `test_m3_validation` and `test_m3_e2e` passed from Lab2's `main`
checkout at `fc52569`. `test_m3_e2e` with the slice-4 contract checks passed from a
synced copy of `26924eb`.

### Operational facts learned

- Lab2's default Docker bridge has no DNS: tests use `docker build --network host`
  and `compose up --no-build`. Compose `build.network: host` triggers an
  interactive bake prompt; do not use it.
- Never put `RLIMIT_FSIZE` on `dangerzone-cli`: gVisor's sparse memory file crashes.
  The provider bounds host files by polling.
- Dangerzone runs its sandbox with `--cgroups=disabled`; the provider's systemd user
  unit limits (3 GiB, 200% CPU, 256 tasks, idle I/O) contain `runsc`/`gvisor_sentry`.
- Staged CDR output: 0640, UID 10004, group 10010 (validation worker `group_add`).

### Partial / uncommitted work

None. The worktree is clean at the handoff commit.

### Blockers and user actions

1. Accept (or reject) the measured CDR approach: the M0 gate checkbox.
2. Before deploying the `cdr` profile: run `hosts/lab2/pdf-cdr-provider/install.sh`
   as `potato`. Enable linger (`loginctl enable-linger potato`; may need sudo/polkit).
   Choose host storage ownership (quarantine is written by UID 10001 with umask 022;
   workers 10002–10004 read it). Deployment is deliberately not done; the archive
   consumer (M4) does not exist yet.
3. `potato` is in the `docker` group (root-equivalent). The provider's rootless
   boundary does not protect against that account; this is a residual risk to record in M8.

## Next implementation order

1. M4 portable archive: consume `pdf.gateway.archive.v1`. Publish only artifacts in
   `FINAL_VALIDATED` with a matching `final_validations` PASS. Any embedded-metadata
   write produces a **new** artifact that must go back through the validation worker
   (extend V004's guard to that artifact kind). Then taxonomy/paths, sidecars,
   atomic publish, reindex without PostgreSQL.
2. M5 real mobile frontend/PWA.
3. M6 exactly-once Paperless projection and outage recovery.
4. M7 optional OCR/AI with offline-safe behavior.
5. M8 hardening, measured Lab2 impact, monitoring, encrypted backup and clean restore.

### First command for the next agent

```bash
cd /home/bro/.codex-worktrees/pdf-security-gateway && git fetch origin && git status --short && git log --oneline -8 && ssh lab2 'cd /home/potato/batlab && git log --oneline -1 && pdf-gateway/tools/verify_cdr_runtime.sh'
```

## M3 plan as written before implementation (completed; kept for history)

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
