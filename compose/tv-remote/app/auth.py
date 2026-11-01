"""The page's own login: one password, and sessions the server can end."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from config import AUTH_FILE, SESSIONS_FILE

# A login of the page's own, not Caddy's basic auth: a phone's home-screen
# app cannot show the browser's password prompt (iOS opened a black screen,
# Android could not fetch the icon). The password is stored as an scrypt hash.
# A session is a random token, kept here only as its hash, so logging out ends it
# for real and a stolen sessions file logs nobody in. It lasts SESSION_DAYS from
# the last renewal, so a device in use stays logged in.

SESSION_DAYS = 30
RENEW_AFTER = 86400
_COOKIE = re.compile(r"(?:^|;\s*)tvr=([A-Za-z0-9_-]{43})(?:;|$)")
_sessions_lock = threading.Lock()
_fails: dict[str, list[float]] = {}


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 2**20, dklen=32)


def set_password(password: str) -> None:
    salt = secrets.token_bytes(16)
    data = {"salt": salt.hex(), "hash": _scrypt(password, salt).hex()}
    log_out_everywhere()
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


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load_sessions() -> dict[str, float]:
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_sessions(sessions: dict[str, float]) -> None:
    tmp = SESSIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sessions))
    tmp.chmod(0o600)
    tmp.replace(SESSIONS_FILE)


def _token(cookie_header: str | None) -> str | None:
    m = _COOKIE.search(cookie_header or "")
    return m.group(1) if m else None


def make_session() -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _sessions_lock:
        sessions = {h: exp for h, exp in _load_sessions().items() if exp > now}
        sessions[_hash(token)] = now + SESSION_DAYS * 86400
        _save_sessions(sessions)
    return token


def valid_session(cookie_header: str | None) -> bool:
    token = _token(cookie_header)
    if not token or not _auth():
        return False
    with _sessions_lock:
        return _load_sessions().get(_hash(token), 0) > time.time()


def renew_session(cookie_header: str | None) -> bool:
    """Extends a session in use, at most once a day. True if it was extended, so
    the cookie should be sent again with its new lifetime."""
    token = _token(cookie_header)
    if not token:
        return False
    full = time.time() + SESSION_DAYS * 86400
    with _sessions_lock:
        sessions = _load_sessions()
        key = _hash(token)
        if sessions.get(key, 0) <= time.time() or full - sessions[key] < RENEW_AFTER:
            return False
        sessions[key] = full
        _save_sessions(sessions)
    return True


def session_cookie_value(cookie_header: str | None) -> str | None:
    return _token(cookie_header)


def end_session(cookie_header: str | None) -> None:
    token = _token(cookie_header)
    if token:
        with _sessions_lock:
            sessions = _load_sessions()
            if sessions.pop(_hash(token), None) is not None:
                _save_sessions(sessions)


def log_out_everywhere() -> None:
    with _sessions_lock:
        _save_sessions({})


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
