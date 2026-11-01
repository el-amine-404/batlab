"""The page's own login: one password, signed session cookies."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time

from config import AUTH_FILE

# A login of the page's own, not Caddy's basic auth: a phone's home-screen
# app cannot show the browser's password prompt (iOS opened a black screen,
# Android could not fetch the icon). The password is stored as an scrypt hash;
# a session is a signed cookie, so nothing is kept per device.

SESSION_DAYS = 180
_fails: dict[str, list[float]] = {}


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 2**20, dklen=32)


def set_password(password: str) -> None:
    salt = secrets.token_bytes(16)
    data = {"salt": salt.hex(), "hash": _scrypt(password, salt).hex(),
            "secret": secrets.token_hex(32)}  # a new secret logs every device out
    tmp = AUTH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.chmod(0o600)
    tmp.replace(AUTH_FILE)


def _auth() -> dict | None:
    try:
        return json.loads(AUTH_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None


def check_password(password: str) -> bool:
    a = _auth()
    return bool(a) and hmac.compare_digest(_scrypt(password, bytes.fromhex(a["salt"])).hex(), a["hash"])


def make_session() -> str:
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(bytes.fromhex(_auth()["secret"]), exp.encode(), "sha256").hexdigest()
    return f"{exp}.{sig}"


def valid_session(cookie_header: str | None) -> bool:
    a = _auth()
    if not a or not cookie_header:
        return False
    m = re.search(r"(?:^|;\s*)tvr=(\d+)\.([0-9a-f]{64})", cookie_header)
    if not m or int(m.group(1)) < time.time():
        return False
    want = hmac.new(bytes.fromhex(a["secret"]), m.group(1).encode(), "sha256").hexdigest()
    return hmac.compare_digest(want, m.group(2))


def too_many_tries(who: str) -> bool:
    """At most 10 wrong passwords per address in 10 minutes."""
    now = time.time()
    tries = [t for t in _fails.get(who, []) if now - t < 600]
    _fails[who] = tries
    return len(tries) >= 10


def note_failure(who: str) -> None:
    _fails.setdefault(who, []).append(time.time())


def clear_failures(who: str) -> None:
    _fails.pop(who, None)


def password_is_set() -> bool:
    return _auth() is not None
