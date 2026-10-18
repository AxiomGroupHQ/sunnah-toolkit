"""User accounts for the optional sign-in (api/accounts.py).

SQLite at USERS_DB_PATH (deploy/macos/run-api.sh sets it). Every account has
an email address that has been proven: it is only created by clicking an
emailed link or by a provider that vouches for the address. That is what
makes linking a Google or GitHub sign-in to an existing account by email safe.

Secrets are never stored as given: session ids and emailed tokens are kept as
SHA-256 hashes, passwords as argon2id hashes. A stolen database file cannot be
replayed as a cookie or a link.

Other tables (feedback on search results, later) reference users(id).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE,  -- lower-cased
    name              TEXT,
    password_hash     TEXT,                  -- argon2id; NULL for link/provider-only accounts
    email_verified_at INTEGER NOT NULL,      -- unix seconds
    created_at        INTEGER NOT NULL,
    last_login_at     INTEGER
);
CREATE TABLE IF NOT EXISTS identities (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider         TEXT NOT NULL,          -- google | github
    provider_user_id TEXT NOT NULL,
    email            TEXT,                   -- as the provider reported it when linked
    created_at       INTEGER NOT NULL,
    UNIQUE (provider, provider_user_id)
);
CREATE INDEX IF NOT EXISTS identities_user ON identities(user_id);
CREATE TABLE IF NOT EXISTS email_tokens (
    token_hash TEXT PRIMARY KEY,             -- sha256 of the token in the link
    purpose    TEXT NOT NULL,                -- verify | magic | reset
    email      TEXT NOT NULL,
    data       TEXT,                         -- verify: JSON {password_hash, name} from the sign-up form
    return_to  TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at    INTEGER
);
CREATE INDEX IF NOT EXISTS email_tokens_expiry ON email_tokens(expires_at);
CREATE TABLE IF NOT EXISTS sessions (
    id_hash    TEXT PRIMARY KEY,             -- sha256 of the id in the cookie
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
"""

_hasher = PasswordHasher()
# Verified against when the email is unknown, so a miss takes as long as a hit.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))

_ready: set[str] = set()
_ready_lock = threading.Lock()


def db_path() -> str | None:
    return os.environ.get("USERS_DB_PATH", "").strip() or None


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """One short-lived connection per operation, committed on success."""
    path = db_path()
    if not path:
        raise RuntimeError("USERS_DB_PATH is not set")
    with _ready_lock:
        if path not in _ready:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            c = sqlite3.connect(path)
            try:
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript(SCHEMA)
            finally:
                c.close()
            _ready.add(path)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def now() -> int:
    return int(time.time())


def sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ── Passwords ──────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def check_password(user: dict[str, Any] | None, password: str) -> bool:
    """True if `password` is right for `user`. Upgrades old hash parameters."""
    stored = user.get("password_hash") if user else None
    try:
        _hasher.verify(stored or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    if not stored:
        return False
    if _hasher.check_needs_rehash(stored):
        with connect() as c:
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user["id"]))
    return True


# ── Users ──────────────────────────────────────────────────────────────────────

def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def get_user(user_id: int) -> dict[str, Any] | None:
    with connect() as c:
        return _row(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def user_by_email(email: str) -> dict[str, Any] | None:
    with connect() as c:
        return _row(c.execute("SELECT * FROM users WHERE email = ?", (normalize_email(email),)).fetchone())


def _get_or_create(c: sqlite3.Connection, email: str, name: str | None = None,
                   password_hash: str | None = None) -> tuple[dict[str, Any], bool]:
    email = normalize_email(email)
    row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is not None:
        if name and not row["name"]:
            c.execute("UPDATE users SET name = ? WHERE id = ?", (name, row["id"]))
        return dict(row), False
    t = now()
    cur = c.execute(
        "INSERT INTO users (email, name, password_hash, email_verified_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, name or None, password_hash, t, t))
    return dict(c.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()), True


def set_password(user_id: int, password_hash: str) -> None:
    with connect() as c:
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def touch_login(user_id: int) -> None:
    with connect() as c:
        c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now(), user_id))


def user_for_identity(provider: str, provider_user_id: str, email: str | None,
                      name: str | None) -> tuple[dict[str, Any], str]:
    """The account for a provider sign-in, and how it was found.

    Returns (user, how) with how one of "identity" (signed in this way
    before), "linked" (attached to the account that already had this verified
    email), or "created". Callers must only pass an email the provider has
    verified.
    """
    with connect() as c:
        row = c.execute(
            "SELECT u.* FROM identities i JOIN users u ON u.id = i.user_id"
            " WHERE i.provider = ? AND i.provider_user_id = ?", (provider, provider_user_id)).fetchone()
        if row is not None:
            return dict(row), "identity"
        if not email:
            raise ValueError("provider gave no verified email")
        user, created = _get_or_create(c, email, name)
        c.execute(
            "INSERT INTO identities (user_id, provider, provider_user_id, email, created_at) VALUES (?, ?, ?, ?, ?)",
            (user["id"], provider, provider_user_id, normalize_email(email), now()))
        return user, "created" if created else "linked"


# ── Emailed tokens ─────────────────────────────────────────────────────────────

TOKEN_TTL = {"verify": 24 * 3600, "magic": 15 * 60, "reset": 30 * 60}


def issue_token(purpose: str, email: str, *, data: str | None = None, return_to: str | None = None) -> str:
    """A new single-use token for a link. Earlier unused ones of the same kind stop working."""
    token = secrets.token_urlsafe(32)
    t = now()
    email = normalize_email(email)
    with connect() as c:
        c.execute("UPDATE email_tokens SET used_at = ? WHERE email = ? AND purpose = ? AND used_at IS NULL",
                  (t, email, purpose))
        c.execute("DELETE FROM email_tokens WHERE expires_at < ?", (t - 7 * 86400,))
        c.execute(
            "INSERT INTO email_tokens (token_hash, purpose, email, data, return_to, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sha256(token), purpose, email, data, return_to, t, t + TOKEN_TTL[purpose]))
    return token


def peek_token(purpose: str, token: str) -> dict[str, Any] | None:
    """The token's row if it is still usable, without using it up."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM email_tokens WHERE token_hash = ? AND purpose = ? AND used_at IS NULL AND expires_at > ?",
            (sha256(token or ""), purpose, now())).fetchone()
    return _row(row)


def use_token(purpose: str, token: str) -> dict[str, Any] | None:
    """Mark the token used and return its row; None if unknown, used or expired."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM email_tokens WHERE token_hash = ? AND purpose = ? AND used_at IS NULL AND expires_at > ?",
            (sha256(token or ""), purpose, now())).fetchone()
        if row is None:
            return None
        c.execute("UPDATE email_tokens SET used_at = ? WHERE token_hash = ?", (now(), row["token_hash"]))
    return dict(row)


def user_for_email_link(email: str, *, name: str | None = None,
                        password_hash: str | None = None) -> tuple[dict[str, Any], bool]:
    """The account for an address that just proved itself by link. (user, created)

    A sign-up's password only applies to the account it creates: an existing
    account keeps its own password, so a sign-up link can never be used to
    change someone else's.
    """
    with connect() as c:
        return _get_or_create(c, email, name, password_hash)


# ── Sessions ───────────────────────────────────────────────────────────────────

def create_session(user_id: int, ttl: int) -> tuple[str, int]:
    """(session id for the cookie, expiry)."""
    sid = secrets.token_urlsafe(32)
    t = now()
    with connect() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (t,))
        c.execute("INSERT INTO sessions (id_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                  (sha256(sid), user_id, t, t + ttl))
    return sid, t + ttl


def session_user(sid: str) -> tuple[dict[str, Any], int] | None:
    """(user, session expiry) for a live session id."""
    with connect() as c:
        row = c.execute(
            "SELECT u.*, s.expires_at AS session_expires_at FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.id_hash = ? AND s.expires_at > ?", (sha256(sid), now())).fetchone()
    if row is None:
        return None
    user = dict(row)
    return user, user.pop("session_expires_at")


def extend_session(sid: str, expires_at: int) -> None:
    with connect() as c:
        c.execute("UPDATE sessions SET expires_at = ? WHERE id_hash = ?", (expires_at, sha256(sid)))


def end_session(sid: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM sessions WHERE id_hash = ?", (sha256(sid),))


def end_all_sessions(user_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
