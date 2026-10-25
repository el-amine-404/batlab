# PDF Security Gateway

This directory is the clean implementation of the v2 specification. It is not
the archived prototype. M1 provides the authenticated API, quarantine, database,
and durable job outbox; later milestones provide scanners and document release.
Capabilities must be promoted milestone by milestone in
`../docs/pdf-gateway/verification.md`.

Run the dependency-free contract checks with:

```bash
python3 pdf-gateway/tools/validate_contracts.py
```

Production deployment is intentionally absent until M1 passes its security gate.
The production-shaped Compose definition is now tracked, but it must not be
deployed as a usable document pipeline until the verified Dangerzone CDR provider
is available. No service may report a document as released before then.

Run the M1 disposable integration gate with:

```bash
pdf-gateway/tests/test_m1.sh
```
