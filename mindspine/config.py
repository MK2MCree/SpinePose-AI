from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


def _read_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _read_csv(name: str, default: str = "") -> Tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    environment: str
    production: bool
    data_dir: Path
    session_secret: str
    session_cookie_name: str
    session_cookie_secure: bool
    session_ttl_seconds: int
    max_cases_per_session: int
    max_upload_bytes: int
    max_batch_files: int
    batch_workers: int
    allowed_origins: Tuple[str, ...]
    trusted_hosts: Tuple[str, ...]
    proxy_secret: str
    expose_docs: bool


def load_settings(project_root: str) -> Settings:
    environment = os.getenv("MINDSPINE_ENV", "development").strip().lower()
    production = environment in {"production", "prod"}

    configured_data_dir = os.getenv("MINDSPINE_DATA_DIR", "").strip()
    data_dir = Path(configured_data_dir or Path(project_root) / "temp_uploads").expanduser().resolve()

    session_secret = os.getenv("MINDSPINE_SESSION_SECRET", "").strip()
    if production and len(session_secret) < 32:
        raise RuntimeError("MINDSPINE_SESSION_SECRET must contain at least 32 characters in production")
    if not session_secret:
        session_secret = secrets.token_urlsafe(48)

    default_origins = (
        "http://127.0.0.1:8000,http://localhost:8000,"
        "http://127.0.0.1:3000,http://localhost:3000"
    )

    return Settings(
        environment=environment,
        production=production,
        data_dir=data_dir,
        session_secret=session_secret,
        session_cookie_name=os.getenv("MINDSPINE_SESSION_COOKIE", "mindspine_session").strip()
        or "mindspine_session",
        session_cookie_secure=_read_bool("MINDSPINE_COOKIE_SECURE", production),
        session_ttl_seconds=_read_int("MINDSPINE_SESSION_TTL_SECONDS", 8 * 60 * 60, 15 * 60, 7 * 24 * 60 * 60),
        max_cases_per_session=_read_int("MINDSPINE_MAX_CASES_PER_SESSION", 12, 1, 100),
        max_upload_bytes=_read_int("MINDSPINE_MAX_UPLOAD_MB", 64, 1, 512) * 1024 * 1024,
        max_batch_files=_read_int("MINDSPINE_MAX_BATCH_FILES", 100, 1, 1000),
        batch_workers=_read_int("MINDSPINE_BATCH_WORKERS", 2, 1, 8),
        allowed_origins=_read_csv("MINDSPINE_ALLOWED_ORIGINS", "" if production else default_origins),
        trusted_hosts=_read_csv("MINDSPINE_TRUSTED_HOSTS", "*" if not production else ""),
        proxy_secret=os.getenv("MINDSPINE_PROXY_SECRET", "").strip(),
        expose_docs=_read_bool("MINDSPINE_EXPOSE_DOCS", not production),
    )

