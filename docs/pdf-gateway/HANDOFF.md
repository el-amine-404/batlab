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
`compose/pdf-gateway/app/tools/lab2_test.sh <test>` (local image builds, 1 s temperature sampling, guard aborts
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
`test_m4_metadata.sh`, `test_m4_gate.sh`, `python3 compose/pdf-gateway/app/tools/validate_contracts.py`, Maven
(`docker run --rm -v "$PWD/pdf-gateway/api:/src" -w /src maven:3.9.11-eclipse-temurin-21@sha256:6fdc855a6ed81d288ca7ca37ac6ff5e9308b612485c0801d70b25a858c83d237 mvn -B -q test`, 4 tests;
remove the root-owned `compose/pdf-gateway/app/api/target` afterwards).

Also local: `test_m5_api.sh`; `test_m5_ui.sh` needs `VENV` (selenium 4.38, playwright 1.56) and
`PLAYWRIGHT_BROWSERS_PATH` with Playwright Firefox installed (`python -m playwright install firefox`, ~97 MB).

Lab2 via `compose/pdf-gateway/app/tools/lab2_test.sh test_m6_paperless`: PASSED (485 s, peak 81 °C).

Lab2 via `compose/pdf-gateway/app/tools/lab2_test.sh`: `test_pipeline_e2e` PASSED (348 s, peak 82 °C).
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

- New PDF versions: VERIFIED since 2026-10-26 (edits write a new version through final validation; see below).
- Automatic scheduled sweeper (`pdf-archive repair`) NOT STARTED (M8). OCR NOT STARTED (M7).
- Rule creation from user corrections needs the M5 UI. OpenAPI lacks formal list/download response schemas.
- Measured impact on Paperless/DNS/Jellyfin latency NOT STARTED (M8).

### Blockers and user actions

1. Deployment decisions (not needed for M5 development): install the provider (`hosts/lab2/pdf-cdr-provider/install.sh`),
   `sudo loginctl enable-linger potato`, choose `PDF_GATEWAY_ARCHIVE_ROOT` (e.g. under `/mnt/storage/data/documents`)
   and host ownership for UIDs 10001–10005 and groups 10002/10010.
2. `potato` is in the `docker` group (root-equivalent): residual risk for M8 (dedicated Dangerzone account recommended).

## M8 in progress (2026-10-25)

- VERIFIED: the gateway lives in `compose/pdf-gateway/app` (commit `0f927bf`), and runs as a homelab stack,
  `compose/pdf-gateway/docker-compose.yml` (commit `a703f16`, plus follow-ups). It uses the repo's
  conventions: image pins in `compose/versions.env`, CPU caps in `hosts/lab2/compose.env`, secrets in
  `compose/.env`, `make setup-pdf-gateway` (also run by `make setup`) for folders and per-stage owners, the
  archive at `${DATA_ROOT}/documents/pdf-archive` (inside restic's `paths.txt`), `pdf-gateway` in
  `hosts/lab2/stacks.txt`, and `docs/services.md`.
  `compose/pdf-gateway/app/tests/test_m8_stack.sh` passed locally: Makefile folders, HTTPS through
  `compose/caddy/conf/pdf.caddy` with Caddy's local CA (`http://pdf.homelab.lan/ca.crt`), Secure/HttpOnly
  cookie, CSRF plus https Origin upload, per-stage uids, fail-closed without CDR, idle Paperless adapter
  without a token, Homepage READ_ONLY card through the web container, and DB unreachable from home_server.
- VERIFIED: `GET /api/v1/system/status` (counts only; `test_m5_api`) and the Homepage `customapi` card.
- IMPLEMENTED, not yet run on Lab2: restic dumps `pdf-gateway-db`; the restore test checks that dump
  and one random archived PDF's SHA-256; the ClamAV signatures, multipart uploads and the socket are excluded.
- User decision: HTTPS for this app only (Caddy `tls internal`, port 443).
- BLOCKED on the user (sudo / access grants): `make setup-pdf-gateway` and `sudo loginctl enable-linger potato`
  on Lab2. The Paperless user/token for the adapter (operations.md step 4) was refused by the
  permission classifier as an access grant, so the user must run it. The first restic backup and
  restore test after deployment also need sudo.
- Lab2 `compose/.env`: gateway secrets generated on Lab2 (backup `compose/.env.bak-before-pdf-gateway`).
  The old unused keys `PDF_GATEWAY_HTTP_PORT`, `PDF_DB_PASSWORD` and `AI_LAPTOP_IP` were left in place.
- Runbook: `docs/pdf-gateway/operations.md`.
- Found on the first real `make up STACK=pdf-gateway` (user): builds on lab2's bridge have no DNS (apt-get
  timed out). The fix is `build: network: host` (as stanzarr). `docker compose build` then asks to allow
  `network.host` and fails without a terminal, so `make build-pdf-gateway` uses plain `docker build
  --network host`, one image at a time, waiting above 75 °C. In a terminal, `make up` asks once to
  allow it, as for stanzarr. Do not feed `yes` into `script` to answer it: that loaded lab2 for 10 minutes.

## AI filing and PDF quality (2026-10-25, user feedback round)

User decisions: the AI's answers apply automatically; a new file waits up to 10 min for the AI; model
`qwen/qwen3-30b-a3b-2507`; Paperless keeps our file (no Paperless setting needed: Paperless 3's
`ARCHIVE_FILE_GENERATION=auto` keeps born-digital PDFs, which the OCR text layer makes ours).
- The file name is never a metadata source (it can be anything).
- Text extraction uses `pdftotext -layout`; dates like `10-Jun-2026` are recognised.
- Dangerzone adds an invisible OCR layer (`PDF_CDR_OCR_LANGUAGE`, one language, default `fra`). Final
  validation admits only Tesseract's GlyphLessFont (pinned FontFile2 SHA-256 `c7845420…`) drawn in
  render mode 3 (`tests/final_validate_font_test.py` with `tests/fixtures/dangerzone-ocr-fra.pdf`).
- The embedded metadata is Title, Author (correspondent), Subject, Keywords (no Producer/Creator since
  2026-10-26: privacy); it's
  linearized, with streams copied unchanged (`test_m4_metadata` asserts `is_linearized`).
- V011: SUGGEST jobs exist before publication (scan_id, context, deadline_at, applied). The archive
  worker defers (no attempt counted) while the agent is ready and the deadline is open. Checked fields
  (`enrichment.validate_filing`, `tests/filing_test.py`) are applied, with review state `AI`. Late answers
  re-file via `reorganise`. Tags come from the vocabulary only (now 64 everyday topics, merged once into
  existing archives).
- The Paperless adapter sends the correspondent, document type, date and tags. Its Paperless user needs
  view/add for correspondents and document types (operations.md step 4; the user must grant them).
- Model evaluation on one of the user's invoices: old prompt (4B) date "1062-20-20", tag "internet";
  new prompt with layout text: 4B ≈17 s, all fields right; 30B ≈61 s, best; qwen2.5-vl-3b with the page
  image was worse (wrong date and amount), so no vision model is used.

## Second feedback round (2026-10-26): commits `603209d` (features + layout), `61a947e` (make up), `fa0f54e` (index rebuild); main merges `b4dc61c`, `5c8dee0`, `42d8874`

User decisions: delete removes everything (Paperless copy to its trash); Discord "received + done" via
`DISCORD_WEBHOOK_DOCUMENTS` (user added it to Lab2 `compose/.env`); plain ASCII names; archive layout
Section/Correspondent with the metadata hidden next to the PDF.

- VERIFIED (tests below + Lab2): browsable archive `archive/<Section>/<Correspondent>/<YYYY-MM-DD Title>.pdf`,
  hidden `.<name>.pdf.json` (sidecar) and `.<name>.pdf.txt`; ` (2)` on collisions; no IDs in names.
  Publish moves text, sidecar, then PDF; repair finishes/removes interrupted publishes, moves, versions.
  V013: unique (archive_path, pdf_file). The worker migrates the first layout at start and rebuilds an
  index naming missing files. Lab2: 6 documents migrated, `verify` ok, index rebuilt (6/6), API serves
  every sidecar and PDF (200). The first start crashed in reindex (nested connection context): fixed in
  `42d8874` with a regression test in `test_m4_worker`.
- VERIFIED by tests: edit every field (UI form), edits re-file and produce a new PDF version through final
  validation (REVISION_VALIDATION_QUEUED → REVISION_PUBLISH_QUEUED, `Archive.replace_pdf`); re-run the AI
  (`POST /archive/{id}/refile`); inline view (`?view=true`, CSP); delete everywhere (DOCUMENT_DELETED fan-out,
  each stage deletes its files; `DELETE /api/v1/scans/{id}`); duplicate upload → 409 + Location (test added
  to `test_m5_api` only on 2026-10-26, it had been untested; on Lab2 a re-upload of an archived file → 409,
  no scan stored); plain
  ASCII names; no Producer; OCR before filing for scans; Paperless content, note, new versions, shared objects.
- VERIFIED on Lab2 (2026-10-26, generated StreamCo invoice, intent ARCHIVE_AND_IMPORT as the UI sends):
  READY in 100 s, filed by the AI, in Paperless (#17) 10 s later, Discord "received" and "done" delivered
  (Apprise "Sent Discord notification"), title edit → version 2 through final validation, Paperless got the
  new version (185 s), delete everywhere → 204, no rows, no archive files, no stage files, empty folder
  removed, Paperless #17 in its trash. 305 s in all, peak 72 °C. (A first run with intent ARCHIVE also
  verified delete without Paperless.)
- VERIFIED on Lab2: `adapter.py share-created` un-owned 5 correspondents, 3 types, 16 tags (its user ID now
  comes from an imported document's owner: `/api/ui_settings/` is 403 for a non-admin, `fb06a0c` fix + M6 check).
- Found on Lab2 and fixed: the model copied the prompt's example title, then (filing-4) chose a known
  correspondent of the same topic for a new sender. Titles must now name only what the text prints
  (`enrichment.title_named_in_text`, `filing_test.py`); filing-5 uses known correspondents for spelling only
  (A/B on qwen3-30b with the stored context: filing-4 wrong, filing-5 right; a real invoice of the known
  correspondent still matches). The prompt and docs no longer quote the user's document titles (they remain
  in Git history before `8e9a962`: titles only, no contents).
- `make up STACK=pdf-gateway` now builds via `build-pdf-gateway` and starts with `--no-build` (verified on
  Lab2, peak 70 °C).
- Tests run 2026-10-26 (laptop), all exit 0: `test_m4_archive`, `test_m4_organize`, `test_m4_worker`,
  `test_m4_gate`, `test_m4_metadata`, `test_m5_api`, `VENV=<scratch venv> test_m5_ui`, `test_m6_paperless`,
  `test_m7_enrichment`. (`test_m5_ui` needs `playwright install firefox` in the venv.)
- DONE by the user 2026-10-26: Paperless permissions for `pdf-gateway` (operations.md step 4 list).
- NEXT: the user re-runs the AI on the CNIE form and the ID card (Re-run the AI); then the remaining M8
  items below (restic backup + restore test need the user's sudo).

## UI/UX round (2026-10-26): commit `0593cdf` (main `94af1e9`)

User request: "Do all of them" (sidebar, previews, estimated time, better progress, and the smaller ideas),
logo and name link to the main page, a Discord message when done.
- VERIFIED by `test_m5_ui` (Firefox desktop + Playwright iPhone): logo/name → main page; sidebar sections and
  correspondents with counts filter the list; thumbnails load; grid view; text search with snippet; attention
  count and list; five-stage progress with "left" estimate on a new upload; inline edit + Undo (revision 4);
  large preview; drop anywhere adds the file; phone Browse sheet. `test_m4_gate`: preview PNG, 400/404 rules,
  search (spacing aside, owner only), list fields. `test_m5_api`: progress endpoint, 404 for others.
  `test_m4_worker`, `test_m6_paperless`, `test_m7_enrichment` pass.
- VERIFIED on Lab2: deployed (peak 66 °C), previews backfilled 6/6, every preview 200 image/png, search
  "m fitness"/"mfitness" → 2 hits, progress medians from Lab2's own runs, web 200.
- Previews live in `pdf-archive/previews/` (archive root, not the browsable tree; rebuilt at worker start).
- NOT DONE: content search is a linear scan of the hidden text files (fine for a personal archive; an index
  would be needed for many thousands of documents). Small previews are PNG (30-190 KB each).

## Next implementation order

1. M8 hardening and operations: adversarial/fuzz/load/disk-full tests; measured host budget (Paperless,
   DNS, Jellyfin latency before/after) on Lab2 within the thermal rule; private metrics and alerts;
   encrypted backup and a clean-host restore drill; scheduled `pdf-archive repair`/verify; deployment
   runbook (provider install, linger, archive root, ownership, Caddy/HTTPS, Homepage `customapi` tile);
   real-iPhone Safari check; rerun the M6 Paperless test; light real-document run (2 documents only).
2. Final Discord notification only when every M0–M8 gate passes.

### First command for the next agent

```bash
cd /home/bro/.codex-worktrees/pdf-security-gateway && git fetch origin && git status --short && git log --oneline -10 && ssh lab2 "docker ps --format '{{.Names}} {{.Status}}' | grep pdf-gateway"
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
