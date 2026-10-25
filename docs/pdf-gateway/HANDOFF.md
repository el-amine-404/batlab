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

## Verified state (2026-10-25 UTC, after M7)

- M0 VERIFIED, including the user's acceptance of the measured CDR approach (a VM was offered and declined).
- M1, M2 VERIFIED. M3 VERIFIED (see `verification.md` gate table). M4 VERIFIED for its spec gate
  (PostgreSQL removed and catalog/tags/contents/index rebuilt from the archive; archive grows with
  Paperless absent; crash-safe publish under a real SIGKILL loop), with listed gaps below.
- M5 VERIFIED locally: browser sessions with CSRF/Origin checks, UI read endpoints, and the `web/` React PWA
  behind a same-origin Caddy container. Tested in headless Firefox (desktop) and Playwright Firefox (exact
  390×844 phone viewport). Real iPhone Safari is NOT STARTED (needs deployment over HTTPS and your phone).
- Real-document check: corpus profiled (703 PDFs, none cryptographically signed). The 2-document pipeline run
  was stopped twice by the thermal guard and is BLOCKED; a lighter real-only run is still possible.
- M6 VERIFIED against a disposable Paperless 3.3.2 on Lab2: exactly-once import via a `gateway_document_id`
  marker field (Paperless 3 accepts duplicates and exposes no checksum), outage/resume, metadata sync,
  `resync`, token never exposed. Bypass paths are documented in `paperless-bypass.md` (not changed).
- M7 VERIFIED on this PC (the AI laptop) with real Tesseract and LM Studio `qwen/qwen3-4b-2507`: pull agent with
  a scoped `ENRICHMENT` token, OCR re-filing through a recoverable move, evidence-checked AI proposals, breaker,
  availability states. Pending: M6 Lab2 rerun after this slice; Arabic OCR fixture.
- Nothing is deployed as a service. The provider unit and the `cdr`/`archive`/`paperless` profiles are installable only.

### Lab2 thermal rule (user requirement, 2026-10-25)

Lab2 runs live services and reached 95.5 °C from uncapped test stacks. Rules now in force:
run tests on the local PC unless they need Dangerzone; run Lab2-only tests **only** through
`pdf-gateway/tools/lab2_test.sh <test>` (local image builds, 1 s temperature sampling, guard aborts
at 85 °C), one at a time, starting below 75 °C. Production CPU caps are configurable
(`PDF_GATEWAY_CPUS_*`, defaults about 4 cores worst case) and the provider is `CPUQuota=100%`.

### Commits (all on `main`, Lab2 pulled)

| Commit | Content |
|---|---|
| `ee8b1e4`, `c160831`, `a8d3eef`, `26924eb` | M3: CDR provider + stage, final validation + guards, e2e, scan contract |
| `3a064fb` (merge) | Resource caps everywhere, thermal guard, CDR acceptance recorded |
| `4745a18` | M4-1 archive library + `pdf-archive` CLI (publish/verify/reindex/repair/export/import) |
| `1bcfe1c` | M4-2 bounded native-text extraction + deterministic organiser, rules schema |
| `a3995b9` | M4-3 durable archive worker, V005 archive index + READY/PUBLISHED guard |
| `38f4f0f` | M4-4 allowlisted embedded metadata revalidated as a new version, V006 |
| `f3718f0` | M4-5 archive API (read-only, RBAC, safe downloads, CAS edits), V007, `reindex-db`, DB-loss gate |
| `501124a` | Budget/healthcheck changes, `lab2_test.sh`, `test_pipeline_e2e.sh` through the archive, docs |
| `25ee7a4` | M5-1 sessions + CSRF, scan/quarantine lists, findings, SSE, safe delete, summary, health, V008 |
| `b3dbb3c` | M5-2/3 web UI, web container, Firefox/Playwright UI tests |
| `5a01bc2` | Paperless adapter, V009, import API, archive-worker hooks, bypass doc, TCP RabbitMQ probes |
| `0d6276d` | M7 enrichment: pull agent, OCR re-filing, AI proposals, V010, UI |

### Pipeline as implemented

`QUEUED → SCANNING → AWAITING_POLICY → ANALYZING (signature) → AWAITING_CDR → SANITIZING →
VALIDATING_OUTPUT → AWAITING_ARCHIVE → ARCHIVING (organise + embed metadata) → VALIDATING_OUTPUT
(metadata version) → AWAITING_ARCHIVE → ARCHIVING (publish) → READY`. With
`PDF_GATEWAY_EMBED_METADATA=false` the CDR output is published directly. Database triggers (V004–V006)
refuse every release state without a PASS for the exact published bytes and an archive record.

### Test commands and latest outcomes (2026-10-25)

Local PC, all passed: `test_m1.sh`, `test_m2.sh`, `test_m2_pipeline.sh`, `test_m3_signatures.sh`,
`test_m3_pipeline.sh`, `test_m4_archive.sh`, `test_m4_organize.sh`, `test_m4_worker.sh`,
`test_m4_metadata.sh`, `test_m4_gate.sh`, `python3 pdf-gateway/tools/validate_contracts.py`, Maven
(`docker run --rm -v "$PWD/pdf-gateway/api:/src" -w /src maven:3.9.11-eclipse-temurin-21@sha256:6fdc855a6ed81d288ca7ca37ac6ff5e9308b612485c0801d70b25a858c83d237 mvn -B -q test`, 4 tests;
remove the root-owned `pdf-gateway/api/target` afterwards).

Also local: `test_m5_api.sh`; `test_m5_ui.sh` needs `VENV` (selenium 4.38, playwright 1.56) and
`PLAYWRIGHT_BROWSERS_PATH` with Playwright Firefox installed (`python -m playwright install firefox`, ~97 MB).

Lab2 via `pdf-gateway/tools/lab2_test.sh test_m6_paperless`: PASSED (485 s, peak 81 °C).

Lab2 via `pdf-gateway/tools/lab2_test.sh`: `test_pipeline_e2e` PASSED (348 s, peak 82 °C).
`test_m3_cdr` and `test_m3_validation` were aborted by the thermal guard with their old caps; caps are now
lowered but they have **not been rerun** (pending, run one at a time when Lab2 is cool).

### Operational facts learned

- Lab2's default Docker bridge has no DNS; Compose `build.network: host` triggers a bake prompt. Build
  locally and load instead (`lab2_test.sh`).
- Never put `RLIMIT_FSIZE` on `dangerzone-cli` (gVisor sparse memory file).
- PL/pgSQL resolves `NEW.<column>` per table: one trigger function cannot guard two tables.
- MuPDF (Dangerzone) writes an inline `/Info` in the catalog; validators must accept that shape.
- `rabbitmq-diagnostics` health probes start an Erlang VM; use `nc -z` under small CPU caps.
- Archive files belong to UID 10005; tests remove them through a container running as that UID.
- Groups: 10010 = staged CDR output and metadata versions (0640); 10002 = analysis text extracts.

### Partial / uncommitted work

None after the checkpoint commit.

### Gaps (explicitly not done)

- `POST /archive/{id}/reissue` (user-requested new PDF version) NOT STARTED; archive versions are 1 only.
- Automatic scheduled sweeper (`pdf-archive repair`) NOT STARTED (M8). OCR NOT STARTED (M7).
- Rule creation from user corrections needs the M5 UI. OpenAPI lacks formal list/download response schemas.
- Measured impact on Paperless/DNS/Jellyfin latency NOT STARTED (M8).

### Blockers and user actions

1. Deployment decisions (not needed for M5 development): install the provider (`hosts/lab2/pdf-cdr-provider/install.sh`),
   `sudo loginctl enable-linger potato`, choose `PDF_GATEWAY_ARCHIVE_ROOT` (e.g. under `/mnt/storage/data/documents`)
   and host ownership for UIDs 10001–10005 and groups 10002/10010.
2. `potato` is in the `docker` group (root-equivalent): residual risk for M8 (dedicated Dangerzone account recommended).

## Next implementation order

1. M8 hardening and operations: adversarial/fuzz/load/disk-full tests; measured host budget (Paperless,
   DNS, Jellyfin latency before/after) on Lab2 within the thermal rule; private metrics and alerts;
   encrypted backup and a clean-host restore drill; scheduled `pdf-archive repair`/verify; deployment
   runbook (provider install, linger, archive root, ownership, Caddy/HTTPS, Homepage `customapi` tile);
   real-iPhone Safari check; rerun the M6 Paperless test; light real-document run (2 documents only).
2. Final Discord notification only when every M0–M8 gate passes.

### First command for the next agent

```bash
cd /home/bro/.codex-worktrees/pdf-security-gateway && git fetch origin && git status --short && git log --oneline -10 && pdf-gateway/tests/test_m7_enrichment.sh
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
