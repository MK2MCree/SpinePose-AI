"""Expiring, revocable login sessions; only token digests are persisted."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import secrets
import sqlite3
import time


class AuthSessions:
    def __init__(self, path, ttl_seconds=28800):
        self.path = str(path)
        self.ttl_seconds = ttl_seconds
        self._memory = sqlite3.connect(':memory:') if self.path == ':memory:' else None
        if not self._memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS auth_sessions (digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL)')

    @contextmanager
    def connection(self):
        db = self._memory or sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            if db is not self._memory:
                db.close()

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    def encode(self, user_id):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.connection() as db:
            db.execute('DELETE FROM auth_sessions WHERE expires <= ?', (now,))
            db.execute('INSERT INTO auth_sessions VALUES (?, ?, ?)', (self.digest(token), user_id, now + self.ttl_seconds))
        return token

    def decode(self, token):
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            return None
        with self.connection() as db:
            row = db.execute('SELECT user_id FROM auth_sessions WHERE digest=? AND expires>?', (self.digest(token), time.time())).fetchone()
        return row[0] if row else None

    def revoke(self, token):
        with self.connection() as db:
            db.execute('DELETE FROM auth_sessions WHERE digest=?', (self.digest(token or ''),))
