# PDF Security Gateway

This directory is the clean implementation of the v2 specification. It is not
the archived prototype and currently contains contracts and verification tools
only. Capabilities must be promoted milestone by milestone in
`../docs/pdf-gateway/verification.md`.

Run the dependency-free contract checks with:

```bash
python3 pdf-gateway/tools/validate_contracts.py
```

Production deployment is intentionally absent until M1 passes its security gate.
In particular, no service may report a document as released while the verified
Dangerzone CDR provider is unavailable.
