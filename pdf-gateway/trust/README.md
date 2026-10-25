# Signature trust store

This directory is mounted read-only into the signature worker. Do not commit
private keys or confidential certificates. To enable explicit trust, place a
reviewed public root bundle on the deployment host (for example `roots.pem`) and
set:

```text
PDF_GATEWAY_SIGNATURE_TRUST_ROOTS=/trust/roots.pem
```

An empty setting is intentional: cryptographically intact signatures are then
reported as `SIGNED_VALID_UNTRUSTED`. The worker never falls back to the host OS
trust store and never fetches certificates or revocation data over the network.
