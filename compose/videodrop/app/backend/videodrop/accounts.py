"""Who may use videodrop, and what each may do: people (a name and a
password, signed in on the page by a session cookie) and API keys (for apps:
clipseerr, Homepage, a script). Each has its own permissions.

Passwords are kept as scrypt hashes, sessions and keys as SHA-256 hashes of
their random tokens: the database holds nothing that signs anyone in. Until
the first person exists, the page asks for a setup code: random, kept in the
database, printed in the log and by `python -m videodrop.admin setup-code`,
so that only someone with access to the server makes the first admin.
"""

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from . import net
from .db import Database
from .domain import Conflict, Invalid, NotFound

# What one may do. admin holds them all.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "download": ("Ask for downloads", "Download links, check them first, and stop, retry or remove their own "
                                      "downloads."),
    "destinations": ("Save into destinations", "Send downloads to the destinations' folders; without it, files are "
                                               "only kept here to download from the page."),
    "everyone": ("See everyone's downloads", "Every download, not only their own; with Ask for downloads, they can "
                                             "stop, retry and remove them too."),
    "settings": ("Change settings", "Settings, destinations, notifications, cookies, Issues and videodrop's log."),
    "admin": ("Admin", "Everything, and the people and API keys."),
}
NAMES = tuple(PERMISSIONS)

SESSION_COOKIE = "videodrop_session"
# A session not used for this long ends; none lasts longer than SESSION_MAX.
SESSION_IDLE = 30 * 86400
SESSION_MAX = 180 * 86400
# last_seen_at is written at most this often.
SESSION_TOUCH = 300
KEY_PREFIX = "vd_"
PASSWORD_MIN = 10
SETUP_CODE = "setup_code"

# scrypt: 2^15 × 8 × 128 B = 32 MiB of memory, about 50 ms a check.
_N, _R, _P = 2 ** 15, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 ** 2, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        found = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                               maxmem=128 * 1024 ** 2, dklen=len(base64.b64decode(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(found, base64.b64decode(digest))


# Checked against when the name is unknown: a wrong name takes as long as a wrong password.
_DUMMY = hash_password(secrets.token_hex(16))


def _code(text: str) -> str:
    return "".join(c for c in text.upper() if c.isalnum())


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < PASSWORD_MIN:
        return f"A password has at least {PASSWORD_MIN} characters."
    if len(password) > 200:
        return "A password has at most 200 characters."
    if username and password.casefold() == username.casefold():
        return "The password can't be the user name."
    return None


def clean_permissions(given) -> list[str]:
    unknown = [name for name in given if name not in PERMISSIONS]
    if unknown:
        raise Invalid(f"Unknown permission {unknown[0]!r}: they are {', '.join(NAMES)}.")
    return [name for name in NAMES if name in given]


@dataclass(frozen=True)
class Principal:
    """Who asks: a person ("user") or an app's API key ("key")."""

    kind: str
    id: str
    name: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    session: str | None = None

    def can(self, permission: str) -> bool:
        return "admin" in self.permissions or permission in self.permissions

    @property
    def owner(self) -> str:
        """What its downloads are marked with: "user:<id>" or "key:<id>"."""
        return f"{self.kind}:{self.id}"

    def public(self) -> dict:
        names = NAMES if "admin" in self.permissions else [n for n in NAMES if n in self.permissions]
        return {"kind": self.kind, "id": self.id, "name": self.name, "permissions": list(names)}


class Throttle:
    """Failed sign-ins by address and by name: after FREE failures in
    WINDOW, each try waits twice as long as the last (30 s, 1 min… up to
    15 min). A success clears the name's."""

    FREE = 5
    WINDOW = 900
    LONGEST = 900

    def __init__(self):
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def wait(self, *keys: str) -> int:
        """Seconds before another try is heard (0: now)."""
        now = time.time()
        longest = 0.0
        with self._lock:
            for key in keys:
                found = self._failures.get(key)
                if not found:
                    continue
                while found and found[0] < now - self.WINDOW:
                    found.popleft()
                if len(found) >= self.FREE:
                    pause = min(self.LONGEST, 30 * 2 ** (len(found) - self.FREE))
                    longest = max(longest, found[-1] + pause - now)
        return max(0, int(longest + 0.999))

    def failed(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.setdefault(key, deque(maxlen=64)).append(time.time())
            if len(self._failures) > 10_000:
                self._failures.clear()

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


class Accounts:
    def __init__(self, db: Database):
        self._db = db
        self._lock = threading.Lock()
        self.throttle = Throttle()

    # ---- the first admin ---------------------------------------------------------------------

    def needs_setup(self) -> bool:
        return self._db.one("SELECT 1 FROM users LIMIT 1") is None

    def setup_code(self) -> str | None:
        """The code the first admin is made with (a new one when there is
        none yet); None once someone exists."""
        if not self.needs_setup():
            self._db.delete(SETUP_CODE)
            return None
        with self._lock:
            code = self._db.get(SETUP_CODE)
            if not code:
                alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
                raw = "".join(secrets.choice(alphabet) for _ in range(12))
                code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
                self._db.put(SETUP_CODE, code)
        return code

    def setup(self, code: str, username: str, name: str, password: str) -> dict:
        """The first person, an admin, when the code is right."""
        with self._lock:
            expected = self._db.get(SETUP_CODE)
            if not self.needs_setup():
                raise Conflict("videodrop is set up already: sign in.")
            # The dashes are for reading: with or without them, any case.
            given, wanted = _code(code), _code(expected or "")
            if not wanted or not hmac.compare_digest(given.encode(), wanted.encode()):
                raise Invalid("That isn't the setup code.")
            user = self._create(username, name, password, ["admin"])
            self._db.delete(SETUP_CODE)
            return user

    # ---- people ------------------------------------------------------------------------------

    def users(self) -> list[dict]:
        return [self._user_public(row) for row in self._db.all("SELECT * FROM users ORDER BY username COLLATE NOCASE")]

    def user(self, user_id: str) -> dict:
        return self._user_public(self._user_row(user_id))

    def _user_row(self, user_id: str) -> sqlite3.Row:
        row = self._db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if not row:
            raise NotFound("There's no such person.")
        return row

    def create_user(self, username: str, name: str, password: str, permissions: list[str]) -> dict:
        with self._lock:
            return self._create(username, name, password, permissions)

    def _create(self, username: str, name: str, password: str, permissions) -> dict:
        username = username.strip()
        if not username or len(username) > 40 or not all(c.isalnum() or c in "._-" for c in username):
            raise Invalid("A user name is letters, digits, . _ and -, up to 40.")
        problem = password_problem(password, username)
        if problem:
            raise Invalid(problem)
        if self._db.one("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)):
            raise Conflict(f"Someone is called {username!r} already.")
        now = time.time()
        user_id = uuid.uuid4().hex[:12]
        self._db.run("INSERT INTO users (id, username, name, password, permissions, enabled, created_at, updated_at, "
                     "password_changed_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
                     (user_id, username, (name or "").strip()[:60] or username, hash_password(password),
                      json.dumps(clean_permissions(permissions)), now, now, now))
        return self.user(user_id)

    def update_user(self, user_id: str, *, name: str | None = None, permissions: list[str] | None = None,
                    enabled: bool | None = None, acting: Principal | None = None) -> dict:
        with self._lock:
            row = self._user_row(user_id)
            changes: dict = {}
            if name is not None:
                changes["name"] = name.strip()[:60] or row["username"]
            if permissions is not None:
                changes["permissions"] = json.dumps(clean_permissions(permissions))
            if enabled is not None:
                changes["enabled"] = int(enabled)
            future_admin = ("admin" in json.loads(changes.get("permissions", row["permissions"]))
                            and bool(changes.get("enabled", row["enabled"])))
            if not future_admin and self._admins(excluding=user_id) == 0:
                raise Conflict("videodrop keeps at least one admin who can sign in: make another first.")
            if acting and acting.kind == "user" and acting.id == user_id and enabled is False:
                raise Conflict("You can't turn yourself off.")
            if changes:
                changes["updated_at"] = time.time()
                assignments = ", ".join(f"{column} = ?" for column in changes)
                self._db.run(f"UPDATE users SET {assignments} WHERE id = ?", (*changes.values(), user_id))
            if enabled is False:
                self._db.run("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            return self.user(user_id)

    def set_password(self, user_id: str, password: str, *, keep_session: str | None = None) -> None:
        """A new password; every session but keep_session ends."""
        row = self._user_row(user_id)
        problem = password_problem(password, row["username"])
        if problem:
            raise Invalid(problem)
        now = time.time()
        self._db.run("UPDATE users SET password = ?, password_changed_at = ?, updated_at = ? WHERE id = ?",
                     (hash_password(password), now, now, user_id))
        self._db.run("DELETE FROM sessions WHERE user_id = ? AND id IS NOT ?", (user_id, keep_session))

    def change_own_password(self, who: Principal, current: str, new: str) -> None:
        row = self._user_row(who.id)
        if not check_password(current, row["password"]):
            raise Invalid("That isn't your current password.")
        self.set_password(who.id, new, keep_session=who.session)

    def delete_user(self, user_id: str, acting: Principal | None = None) -> None:
        with self._lock:
            row = self._user_row(user_id)
            if acting and acting.kind == "user" and acting.id == user_id:
                raise Conflict("You can't remove yourself.")
            if "admin" in json.loads(row["permissions"]) and self._admins(excluding=user_id) == 0:
                raise Conflict("videodrop keeps at least one admin who can sign in.")
            self._db.run("DELETE FROM users WHERE id = ?", (user_id,))

    def _admins(self, excluding: str) -> int:
        return sum(1 for row in self._db.all("SELECT permissions FROM users WHERE enabled = 1 AND id != ?", (excluding,))
                   if "admin" in json.loads(row["permissions"]))

    @staticmethod
    def _user_public(row: sqlite3.Row) -> dict:
        return {"id": row["id"], "username": row["username"], "name": row["name"],
                "permissions": json.loads(row["permissions"]), "enabled": bool(row["enabled"]),
                "created_at": row["created_at"], "last_login_at": row["last_login_at"],
                "password_changed_at": row["password_changed_at"]}

    # ---- signing in ------------------------------------------------------------------------------

    def sign_in(self, username: str, password: str, address: str, agent: str) -> tuple[str, dict]:
        """(the session's token, the person); Invalid for a wrong name or
        password (the same sentence for both)."""
        row = self._db.one("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),))
        good = check_password(password, row["password"] if row else _DUMMY)
        if not row or not good or not row["enabled"]:
            raise Invalid("Wrong user name or password." if not row or not good else
                          "This account is turned off: ask an admin.")
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._db.run("INSERT INTO sessions (id, user_id, created_at, expires_at, last_seen_at, address, agent) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (token_hash(token), row["id"], now, now + SESSION_IDLE, now, address[:64], agent[:200]))
        self._db.run("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
        self._db.run("DELETE FROM sessions WHERE expires_at < ?", (now,))
        return token, self._user_public(row)

    def session(self, token: str) -> Principal | None:
        if not token or len(token) > 200:
            return None
        key = token_hash(token)
        row = self._db.one("SELECT sessions.*, users.name, users.permissions, users.enabled FROM sessions "
                           "JOIN users ON users.id = sessions.user_id WHERE sessions.id = ?", (key,))
        now = time.time()
        if not row or not row["enabled"] or row["expires_at"] < now:
            return None
        if now - row["last_seen_at"] > SESSION_TOUCH:
            self._db.run("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                         (now, min(now + SESSION_IDLE, row["created_at"] + SESSION_MAX), key))
        return Principal("user", row["user_id"], row["name"], frozenset(json.loads(row["permissions"])), key)

    def sign_out(self, session: str) -> None:
        self._db.run("DELETE FROM sessions WHERE id = ?", (session,))

    def sessions(self, user_id: str) -> list[dict]:
        return [{"id": row["id"][:12], "created_at": row["created_at"], "last_seen_at": row["last_seen_at"],
                 "address": row["address"], "host": net.name_of(row["address"]), "agent": row["agent"]}
                for row in self._db.all("SELECT * FROM sessions WHERE user_id = ? AND expires_at >= ? "
                                        "ORDER BY last_seen_at DESC", (user_id, time.time()))]

    def sign_out_everywhere(self, user_id: str, keep: str | None = None) -> int:
        return self._db.run("DELETE FROM sessions WHERE user_id = ? AND id IS NOT ?", (user_id, keep))

    # ---- API keys ----------------------------------------------------------------------------

    def keys(self) -> list[dict]:
        return [self._key_public(row) for row in self._db.all("SELECT * FROM api_keys ORDER BY name COLLATE NOCASE")]

    def create_key(self, name: str, permissions: list[str], created_by: str | None) -> tuple[str, dict]:
        """(the key, shown this once; its description)."""
        name = name.strip()
        if not name or len(name) > 40 or not all(c.isalnum() or c in " ._-" for c in name):
            raise Invalid("A key's name is letters, digits, spaces, . _ and -, up to 40: the app it is for.")
        if "admin" in permissions:
            raise Invalid("An API key can't be an admin: people and keys are managed by a person.")
        with self._lock:
            if self._db.one("SELECT 1 FROM api_keys WHERE name = ? COLLATE NOCASE", (name,)):
                raise Conflict(f"A key is called {name!r} already.")
            token = KEY_PREFIX + secrets.token_urlsafe(32)
            key_id = uuid.uuid4().hex[:12]
            self._db.run("INSERT INTO api_keys (id, name, prefix, hash, permissions, created_by, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (key_id, name, token[:10], token_hash(token), json.dumps(clean_permissions(permissions)),
                          created_by, time.time()))
        return token, self._key_public(self._db.one("SELECT * FROM api_keys WHERE id = ?", (key_id,)))

    def update_key(self, key_id: str, permissions: list[str]) -> dict:
        if "admin" in permissions:
            raise Invalid("An API key can't be an admin.")
        if not self._db.run("UPDATE api_keys SET permissions = ? WHERE id = ?",
                            (json.dumps(clean_permissions(permissions)), key_id)):
            raise NotFound("There's no such key.")
        return self._key_public(self._db.one("SELECT * FROM api_keys WHERE id = ?", (key_id,)))

    def delete_key(self, key_id: str) -> None:
        if not self._db.run("DELETE FROM api_keys WHERE id = ?", (key_id,)):
            raise NotFound("There's no such key.")

    def key(self, token: str, address: str) -> Principal | None:
        if not token.startswith(KEY_PREFIX) or len(token) > 200:
            return None
        row = self._db.one("SELECT * FROM api_keys WHERE hash = ?", (token_hash(token),))
        if not row:
            return None
        now = time.time()
        if not row["last_used_at"] or now - row["last_used_at"] > SESSION_TOUCH or row["last_address"] != address:
            self._db.run("UPDATE api_keys SET last_used_at = ?, last_address = ? WHERE id = ?",
                         (now, address[:64], row["id"]))
        return Principal("key", row["id"], row["name"], frozenset(json.loads(row["permissions"])))

    @staticmethod
    def _key_public(row: sqlite3.Row) -> dict:
        return {"id": row["id"], "name": row["name"], "prefix": row["prefix"],
                "permissions": json.loads(row["permissions"]), "created_at": row["created_at"],
                "last_used_at": row["last_used_at"], "last_address": row["last_address"],
                "last_host": net.name_of(row["last_address"])}
