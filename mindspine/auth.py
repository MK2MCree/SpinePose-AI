from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


USERNAME_RE = re.compile(r"^[\w.\-\u4e00-\u9fff]{2,32}$", re.UNICODE)
PASSWORD_MIN_LENGTH = 8
PASSWORD_ITERATIONS = 600_000
MAX_AVATAR_BYTES = 2 * 1024 * 1024
ALLOWED_AVATAR_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


class AccountValidationError(ValueError):
    """A user-facing account input error."""


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    username: str
    avatar_url: Optional[str]
    created_at: int

    def as_dict(self) -> dict:
        return {
            "id": self.user_id,
            "username": self.username,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at,
        }


class UserStore:
    """Small local-only account store backed by SQLite.

    Connections are short-lived so FastAPI worker threads do not share a
    sqlite cursor. Password hashes and avatar bytes never leave this module.
    """

    def __init__(self, database_path: Path):
        self._memory_connection: Optional[sqlite3.Connection] = None
        if str(database_path) == ":memory:":
            self.database_path = Path(":memory:")
            self._memory_connection = sqlite3.connect(":memory:", timeout=10)
            self._memory_connection.row_factory = sqlite3.Row
        else:
            self.database_path = Path(database_path)
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(str(self.database_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    avatar_data BLOB,
                    avatar_mime TEXT,
                    created_at INTEGER NOT NULL,
                    last_login_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_username_key ON users(username_key)"
            )

    @staticmethod
    def _username_key(username: str) -> str:
        normalized = (username or "").strip()
        if not USERNAME_RE.fullmatch(normalized):
            raise AccountValidationError("用户名需为 2–32 位，可使用中文、字母、数字、下划线、点或连字符")
        return normalized.casefold()

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password or "") < PASSWORD_MIN_LENGTH:
            raise AccountValidationError(f"密码至少需要 {PASSWORD_MIN_LENGTH} 位")
        if len(password) > 256:
            raise AccountValidationError("密码不能超过 256 位")

    @staticmethod
    def _hash_password(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            PASSWORD_ITERATIONS,
        )

    @staticmethod
    def _profile(row: sqlite3.Row) -> UserProfile:
        user_id = str(row["id"])
        return UserProfile(
            user_id=user_id,
            username=str(row["username"]),
            avatar_url=f"/api/auth/avatar/{user_id}" if row["avatar_data"] else None,
            created_at=int(row["created_at"]),
        )

    def register(
        self,
        username: str,
        password: str,
        avatar_data: bytes | None = None,
        avatar_mime: str | None = None,
    ) -> UserProfile:
        username = (username or "").strip()
        username_key = self._username_key(username)
        self._validate_password(password)
        self._validate_avatar(avatar_data, avatar_mime)

        salt = secrets.token_bytes(16)
        now = int(time.time())
        user_id = uuid.uuid4().hex
        password_hash = self._hash_password(password, salt)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO users (
                        id, username, username_key, password_salt, password_hash,
                        avatar_data, avatar_mime, created_at, last_login_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        username,
                        username_key,
                        salt,
                        password_hash,
                        avatar_data,
                        avatar_mime,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            if "username_key" in str(error).lower():
                raise AccountValidationError("该用户名已被使用") from error
            raise
        return UserProfile(
            user_id=user_id,
            username=username,
            avatar_url=f"/api/auth/avatar/{user_id}" if avatar_data else None,
            created_at=now,
        )

    def authenticate(self, username: str, password: str) -> Optional[UserProfile]:
        try:
            username_key = self._username_key(username)
        except AccountValidationError:
            return None
        if not password:
            return None

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_key = ?", (username_key,)
            ).fetchone()
            if row is None:
                return None
            computed_hash = self._hash_password(password, bytes(row["password_salt"]))
            if not hmac.compare_digest(computed_hash, bytes(row["password_hash"])):
                return None
            connection.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (int(time.time()), row["id"]),
            )
            return self._profile(row)

    def get_user(self, user_id: str | None) -> Optional[UserProfile]:
        if not user_id or not re.fullmatch(r"[0-9a-f]{32}", user_id):
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._profile(row) if row else None

    def get_avatar(self, user_id: str) -> Optional[tuple[bytes, str]]:
        if not re.fullmatch(r"[0-9a-f]{32}", user_id or ""):
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT avatar_data, avatar_mime FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if not row or not row["avatar_data"]:
            return None
        return bytes(row["avatar_data"]), str(row["avatar_mime"] or "image/png")

    @staticmethod
    def _validate_avatar(avatar_data: bytes | None, avatar_mime: str | None) -> None:
        if avatar_data is None:
            return
        if not avatar_data:
            raise AccountValidationError("头像文件为空")
        if len(avatar_data) > MAX_AVATAR_BYTES:
            raise AccountValidationError("头像文件不能超过 2 MB")
        if avatar_mime not in ALLOWED_AVATAR_MIME_TYPES:
            raise AccountValidationError("头像仅支持 JPG、PNG 或 WebP 格式")
