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

Before starting a new milestone slice, check the active goal/usage budget. Do not
start a slice that cannot reasonably be implemented, tested, documented, and
committed within the remaining budget.

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
- Main on Lab2 currently includes merge `1273e18`.

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

1. M3: pyHanko signature evidence, immutable signed originals, Dangerzone adapter, final-byte rescan/revalidation, safe visual/handwritten signature fixture checks.
2. M4 portable archive and database-less reconstruction before any Paperless integration.
3. M5 real mobile frontend/PWA.
4. M6 exactly-once Paperless projection and outage recovery.
5. M7 optional OCR/AI with offline-safe behavior.
6. M8 hardening, measured Lab2 impact, monitoring, encrypted backup and clean restore.

Never skip a milestone gate or substitute mocked security verdicts for actual
engine execution.
