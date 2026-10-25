# Architecture and decisions

## Deployment split

The initial modular monorepo has these independently deployable boundaries:

1. `api`: Java 21/Spring Boot orchestration, REST/SSE, auth, policy, audit, and outbox.
2. `web`: stateless React client; it never mounts document storage.
3. `postgres` and `rabbitmq`: private operational services with no host ports.
4. `analysis-worker`: bounded ClamAV stream client and disposable analysis tools.
5. `cdr-adapter`: narrow client for a verified Dangerzone execution provider.
6. `archive-worker`: sole writer for staging/archive/manifests.
7. Optional OCR, Paperless, and AI adapters that cannot authorize release.

Queue messages contain opaque job/artifact IDs, stage, attempt, policy version,
and trace ID—never document bytes, host paths, credentials, or extracted content.

## Recorded decisions

- **ADR-001:** The portable filesystem archive is authoritative. Database loss is
  recoverable by validating sidecars and rebuilding the index.
- **ADR-002:** CDR is fail-closed and external to ordinary Compose isolation. The
  API depends on a capability-reported adapter, not Docker socket access.
- **ADR-003:** Files are streamed to generated quarantine paths while hashing.
  Original names are display-only untrusted metadata.
- **ADR-004:** Archive publication is a same-filesystem atomic directory rename.
  No reader can observe staging.
- **ADR-005:** Sidecar edits use revision ETags and do not rewrite immutable PDF
  bytes. Reissuing embedded metadata produces and revalidates a new version.
- **ADR-006:** Classification is deterministic first; AI may propose bounded
  fields with evidence but cannot select paths or security verdicts.
- **ADR-007:** Paperless is eventually consistent and exactly-once by immutable
  archive artifact hash/idempotency mapping, never by database or media writes.

## State invariants

- Every transition is append-audited and compare-and-set from an allowed state.
- `READY` requires a `FINAL_VALIDATED` artifact whose stored SHA-256 matches the
  exact published bytes and whose required checks all have successful evidence.
- A transformation invalidates preceding file-level attestations.
- `UNKNOWN` or `FAILED` mandatory checks cannot yield `ALLOW_SANITIZED`.
- Published directories are immutable except atomic, revisioned sidecar updates;
  PDF changes create a new artifact version.

