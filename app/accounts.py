"""
Durable juror accounts and level clears (separate from LangGraph checkpoints).

SQLite file: ACCOUNTS_DB (default data/accounts.db).
Sessions: HMAC-signed tokens (AUTH_SECRET).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

MAX_USERNAME_LEN = 40
MIN_PASSWORD_LEN = 6
MAX_PASSWORD_LEN = 128
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{2,40}$")

_TOKEN_TTL_SECONDS = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", str(60 * 60 * 24 * 30)))
_PBKDF2_ITERS = 210_000

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _accounts_path() -> Path:
    raw = os.getenv("ACCOUNTS_DB", "data/accounts.db")
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _auth_secret() -> bytes:
    secret = (os.getenv("AUTH_SECRET") or "").strip()
    if not secret:
        # Dev-only fallback so local runs work without .env; production must set AUTH_SECRET.
        secret = "dev-only-auth-secret-change-me"
    return secret.encode("utf-8")


def get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            path = _accounts_path()
            _conn = sqlite3.connect(str(path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys = ON")
            _init_schema(_conn)
        return _conn


def reset_conn_for_tests() -> None:
    """Close connection so tests can point ACCOUNTS_DB at a temp file."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL COLLATE NOCASE,
            username_norm TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS level_clears (
            user_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            scenario_id TEXT NOT NULL,
            cleared_at REAL NOT NULL,
            PRIMARY KEY (user_id, level),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()


def _hash_password(password: str, *, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERS,
    )
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERS}$"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(dk).decode('ascii')}"
    )


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_b64, hash_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(hash_b64.encode("ascii"))
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def validate_username(username: str) -> str:
    name = (username or "").strip()
    if not _USERNAME_RE.fullmatch(name):
        raise ValueError(
            "username must be 2–40 characters (letters, digits, underscore)"
        )
    return name


def validate_password(password: str) -> str:
    if not isinstance(password, str):
        raise ValueError("password is required")
    if len(password) < MIN_PASSWORD_LEN or len(password) > MAX_PASSWORD_LEN:
        raise ValueError(
            f"password must be {MIN_PASSWORD_LEN}–{MAX_PASSWORD_LEN} characters"
        )
    return password


def create_user(username: str, password: str) -> dict:
    name = validate_username(username)
    pw = validate_password(password)
    user_id = secrets.token_hex(16)
    now = time.time()
    conn = get_conn()
    try:
        with _lock:
            conn.execute(
                """
                INSERT INTO users (id, username, username_norm, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, name, name.lower(), _hash_password(pw), now),
            )
            conn.commit()
    except sqlite3.IntegrityError as e:
        raise ValueError("username already taken") from e
    return {"id": user_id, "username": name}


def authenticate(username: str, password: str) -> Optional[dict]:
    name = (username or "").strip()
    if not name or not password:
        return None
    conn = get_conn()
    with _lock:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username_norm = ?",
            (name.lower(),),
        ).fetchone()
    if row is None:
        return None
    if not _verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"]}


def get_user(user_id: str) -> Optional[dict]:
    conn = get_conn()
    with _lock:
        row = conn.execute(
            "SELECT id, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"]}


def issue_token(user_id: str) -> str:
    """Return base64url token: user_id.exp.sig"""
    exp = int(time.time()) + _TOKEN_TTL_SECONDS
    body = f"{user_id}.{exp}"
    sig = hmac.new(_auth_secret(), body.encode("utf-8"), hashlib.sha256).digest()
    token = f"{body}.{base64.urlsafe_b64encode(sig).decode('ascii').rstrip('=')}"
    return token


def verify_token(token: str) -> Optional[str]:
    """Return user_id if token is valid, else None."""
    if not token or not isinstance(token, str):
        return None
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    user_id, exp_s, sig_b64 = parts
    try:
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    body = f"{user_id}.{exp_s}"
    expected = hmac.new(_auth_secret(), body.encode("utf-8"), hashlib.sha256).digest()
    pad = "=" * (-len(sig_b64) % 4)
    try:
        got = base64.urlsafe_b64decode(sig_b64 + pad)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected, got):
        return None
    if get_user(user_id) is None:
        return None
    return user_id


def cleared_levels(user_id: str) -> set[int]:
    conn = get_conn()
    with _lock:
        rows = conn.execute(
            "SELECT level FROM level_clears WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {int(r["level"]) for r in rows}


def max_cleared_level(user_id: str) -> int:
    levels = cleared_levels(user_id)
    return max(levels) if levels else 0


def is_level_unlocked(user_id: str, level: int) -> bool:
    """Level 1 always open; level N needs clear of N-1."""
    level = int(level)
    if level <= 1:
        return True
    return (level - 1) in cleared_levels(user_id)


def record_level_clear(user_id: str, level: int, scenario_id: str) -> bool:
    """
    Record a clear. Returns True if newly inserted, False if already cleared.
    """
    conn = get_conn()
    now = time.time()
    with _lock:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO level_clears (user_id, level, scenario_id, cleared_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, int(level), scenario_id, now),
        )
        conn.commit()
        return cur.rowcount > 0


def credit_level_clears(user_ids: list[str], level: int, scenario_id: str) -> list[str]:
    """Credit clears for each user; return user_ids that newly unlocked this level."""
    newly: list[str] = []
    for uid in user_ids:
        if not uid:
            continue
        if record_level_clear(uid, level, scenario_id):
            newly.append(uid)
    return newly
