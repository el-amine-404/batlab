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

## Verified state (2026-10-25 UTC)

- M0 contracts, architecture, threat model, schemas, and capacity plan are tracked.
- M1 is verified: authenticated streaming upload, RBAC, immutable opaque quarantine, disk headroom, PostgreSQL, RabbitMQ transactional outbox, audit, idempotency, ownership, restart durability.
- M2 is verified: live ClamAV definitions and INSTREAM, qpdf, pinned PDFiD/pdf-parser, YARA, typed deterministic outcomes, atomic restricted reports, one-at-a-time durable worker, replay idempotency. Safe fixtures cover clean, active action, EICAR, malformed, encrypted, attachment, stale/unavailable/timed-out ClamAV.
- Commands that passed locally:
  - `pdf-gateway/tests/test_m1.sh`
  - `pdf-gateway/tests/test_m2.sh`
  - `pdf-gateway/tests/test_m2_pipeline.sh`
- Main on Lab2 currently includes merge `1273e18`.

## Current CDR gate

Dangerzone 0.11.1 and its signed image digest
`sha256:b1e2ad0aa19c5b994c5c8e49d74d9c4ee99fba1795cf7162ac7aa103440b9f97`
are installed on Lab2. Signature verification passed. The first installer revision
incorrectly selected host `runsc` as Podman's outer runtime. Official Dangerzone
architecture uses rootless Podman/crun as the networkless outer container and
launches bundled gVisor/runsc inside it. Commit `e808050` corrects this.

User action currently required on Lab2 (interactive sudo):

```bash
cd /home/potato/batlab
sudo hosts/lab2/install-pdf-cdr-runtime.sh
```

After that, run from the development host:

```bash
ssh lab2 'cd /home/potato/batlab && pdf-gateway/tools/verify_cdr_runtime.sh'
```

Do not mark CDR verified unless it prints `CDR_READY` after a real conversion and
the debug evidence shows both inner gVisor/runsc and `--network=none`. Record the
exact result in `verification.md` and update the M0 checklist.

## Next implementation order

1. Close the real CDR isolation gate above.
2. M3: pyHanko signature evidence, immutable signed originals, Dangerzone adapter, final-byte rescan/revalidation, safe visual/handwritten signature fixture checks.
3. M4 portable archive and database-less reconstruction before any Paperless integration.
4. M5 real mobile frontend/PWA.
5. M6 exactly-once Paperless projection and outage recovery.
6. M7 optional OCR/AI with offline-safe behavior.
7. M8 hardening, measured Lab2 impact, monitoring, encrypted backup and clean restore.

Never skip a milestone gate or substitute mocked security verdicts for actual
engine execution.
