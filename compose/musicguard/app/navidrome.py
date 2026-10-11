"""Navidrome scans when musicguard says so.

Navidrome's own scanner is off (ND_SCANNER_ENABLED=false): with it on, a song
Lidarr imported was playable before musicguard had checked it. musicguard asks
for a scan after a library pass that left no file unchecked, so Navidrome only
ever indexes checked files. startScan is admin-only in Navidrome, so
musicguard has an admin account; everyone else reaches Navidrome through
Caddy, which answers startScan itself (compose/caddy/conf/navidrome.caddy).
"""

import hashlib
import json
import secrets
import time
import urllib.parse
import urllib.request


def _call(url, user, password, method):
    salt = secrets.token_hex(8)
    query = urllib.parse.urlencode({
        "u": user, "s": salt, "t": hashlib.md5((password + salt).encode()).hexdigest(),
        "v": "1.16.1", "c": "musicguard", "f": "json",
    })
    with urllib.request.urlopen(f"{url.rstrip('/')}/rest/{method}?{query}", timeout=30) as response:
        answer = json.loads(response.read(1024 * 1024)).get("subsonic-response", {})
    if answer.get("status") != "ok":
        raise RuntimeError((answer.get("error") or {}).get("message") or f"{method} refused")
    return answer


def start_scan(url, user, password, wait=120):
    """Ask for a quick scan and wait (up to wait seconds) for it to finish, so
    "ready" means playable. Raises on any answer but "ok"."""
    _call(url, user, password, "startScan")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(2)
        if not _call(url, user, password, "getScanStatus").get("scanStatus", {}).get("scanning"):
            return
