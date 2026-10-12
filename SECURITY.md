# Security

Please report vulnerabilities privately through GitHub's
[security advisories](https://github.com/el-amine-404/stanzarr/security/advisories/new),
not in public issues.

## What Stanzarr does to stay safe

- One account, password hashed with scrypt; sessions are random tokens stored
  hashed, sent as an `HttpOnly`, `SameSite=Strict` cookie.
- Changes need a JSON body and a same-site `Origin`, so another site cannot act
  on your behalf. Sign-in attempts are limited per address.
- Workers use their own key, which can be revoked from the interface.
- It writes only `.lrc` and `.txt` files beside existing songs, never deletes,
  and backs up any `.lrc` before replacing it. Read-only mode writes nothing.
- The container runs as a regular user and works with a read-only root
  filesystem, no capabilities and `no-new-privileges`.
- Strict Content-Security-Policy; no third-party scripts, fonts or trackers.

Put Stanzarr behind your reverse proxy on a private network or a VPN; it is not
meant to be exposed to the internet.
