"""Accounts, sessions and the worker key.

Passwords are hashed with scrypt. Sessions are random tokens kept hashed in
the database and sent as an HttpOnly, SameSite=Strict cookie. Changes need a
JSON body and a matching Origin, so another site cannot post on your behalf.
"""

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from .db import Database

SESSION_DAYS = 30
COOKIE = "stanzarr_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        again = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(again.hex(), digest)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def has_users(db: Database) -> bool:
    return db.one("SELECT 1 FROM users LIMIT 1") is not None


def create_user(db: Database, username: str, password: str) -> None:
    existing = db.one("SELECT id FROM users WHERE username=?", (username,))
    if existing:
        db.run("UPDATE users SET password=? WHERE id=?", (hash_password(password), existing["id"]))
    else:
        db.run("INSERT INTO users(username, password, created_at) VALUES(?, ?, ?)",
               (username, hash_password(password), int(time.time())))


def login(db: Database, username: str, password: str) -> str | None:
    user = db.one("SELECT * FROM users WHERE username=?", (username,))
    if not user or not check_password(password, user["password"]):
        return None
    token = secrets.token_urlsafe(32)
    db.run("INSERT INTO sessions(token, user_id, expires_at) VALUES(?, ?, ?)",
           (_token_hash(token), user["id"], int(time.time()) + SESSION_DAYS * 86400))
    db.run("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    return token


def session_user(db: Database, token: str | None) -> dict | None:
    if not token:
        return None
    return db.one("SELECT u.id, u.username FROM sessions s JOIN users u ON u.id=s.user_id"
                  " WHERE s.token=? AND s.expires_at > ?", (_token_hash(token), int(time.time())))


def logout(db: Database, token: str | None) -> None:
    if token:
        db.run("DELETE FROM sessions WHERE token=?", (_token_hash(token),))


def worker_key(db: Database, fixed: str = "") -> str:
    if fixed:
        return fixed
    key = db.setting("worker_key")
    if not key:
        key = secrets.token_urlsafe(24)
        db.set_setting("worker_key", key)
    return key


def regenerate_worker_key(db: Database) -> str:
    key = secrets.token_urlsafe(24)
    db.set_setting("worker_key", key)
    return key


class LoginLimiter:
    """At most 10 failed logins per address in 5 minutes."""

    def __init__(self, limit: int = 10, window: int = 300):
        self.limit, self.window = limit, window
        self.failures: dict[str, deque] = defaultdict(deque)

    def blocked(self, address: str) -> bool:
        q = self.failures[address]
        while q and time.time() - q[0] > self.window:
            q.popleft()
        return len(q) >= self.limit

    def failed(self, address: str) -> None:
        self.failures[address].append(time.time())

    def succeeded(self, address: str) -> None:
        self.failures.pop(address, None)
