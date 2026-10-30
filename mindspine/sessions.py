from __future__ import annotations

import copy
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional


CURRENT_SESSION_ID: ContextVar[str] = ContextVar("mindspine_session_id", default="local")
CURRENT_CASE_ID: ContextVar[Optional[str]] = ContextVar("mindspine_case_id", default=None)

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")

_CASE_STATE_DEFAULTS = {
    "current_mode": None,
    "is_batch_mode": False,
    "image_path": None,
    "folder_path": None,
    "image_list": list,
    "raw_pixel_array": None,
    "current_cv_img": None,
    "cv_image_result": None,
    "current_main_img": None,
    "current_sub_img": None,
    "last_bbox": None,
    "current_angles": None,
    "current_slip_list": list,
}


def new_session_id() -> str:
    return secrets.token_hex(16)


def current_session_id() -> str:
    return CURRENT_SESSION_ID.get()


def current_case_id() -> Optional[str]:
    return CURRENT_CASE_ID.get()


class SessionTokenCodec:
    def __init__(self, secret: str):
        self._secret = secret.encode("utf-8")

    def encode(self, session_id: str) -> str:
        signature = hmac.new(self._secret, session_id.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{session_id}.{signature}"

    def decode(self, token: str) -> Optional[str]:
        try:
            session_id, supplied = (token or "").split(".", 1)
        except ValueError:
            return None
        if not _SESSION_ID_RE.fullmatch(session_id):
            return None
        expected = hmac.new(self._secret, session_id.encode("ascii"), hashlib.sha256).hexdigest()
        return session_id if hmac.compare_digest(supplied, expected) else None


@dataclass
class CaseRecord:
    model: Any
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)


@dataclass
class SessionRecord:
    cases: "OrderedDict[str, CaseRecord]" = field(default_factory=OrderedDict)
    current_case_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)


class CaseRegistry:
    """Keeps mutable case state isolated while sharing loaded model weights."""

    def __init__(self, model_template: Any, ttl_seconds: int, max_cases_per_session: int):
        self._template = model_template
        self._ttl_seconds = ttl_seconds
        self._max_cases = max_cases_per_session
        self._sessions: Dict[str, SessionRecord] = {}
        self._lock = threading.RLock()

    def _new_case_model(self) -> Any:
        model = copy.copy(self._template)
        for name, default in _CASE_STATE_DEFAULTS.items():
            setattr(model, name, default() if callable(default) else default)
        return model

    def _session_unlocked(self, session_id: str) -> SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            record = SessionRecord()
            self._sessions[session_id] = record
        record.last_accessed_at = time.time()
        return record

    @contextmanager
    def activate_session(self, session_id: str) -> Iterator[None]:
        token = CURRENT_SESSION_ID.set(session_id)
        case_token = CURRENT_CASE_ID.set(None)
        try:
            with self._lock:
                self._session_unlocked(session_id)
            yield
        finally:
            CURRENT_CASE_ID.reset(case_token)
            CURRENT_SESSION_ID.reset(token)

    @contextmanager
    def activate_case(self, case_id: str, remember: bool = True, create: bool = False) -> Iterator[Any]:
        session_id = current_session_id()
        with self._lock:
            session = self._session_unlocked(session_id)
            record = session.cases.get(case_id)
            if record is None:
                if not create:
                    raise KeyError(case_id)
                if not _CASE_ID_RE.fullmatch(case_id):
                    raise ValueError("Invalid case identifier")
                record = CaseRecord(model=self._new_case_model())
                session.cases[case_id] = record
                while len(session.cases) > self._max_cases:
                    oldest_id, _ = session.cases.popitem(last=False)
                    if session.current_case_id == oldest_id:
                        session.current_case_id = None
            else:
                session.cases.move_to_end(case_id)
            record.last_accessed_at = time.time()
            if remember:
                session.current_case_id = case_id
        token = CURRENT_CASE_ID.set(case_id)
        try:
            yield record.model
        finally:
            CURRENT_CASE_ID.reset(token)

    def create_case(self, case_id: str) -> Any:
        with self.activate_case(case_id, remember=True, create=True) as model:
            created = model
        # Like select_case, keep the request's choice pinned after creation.
        # Another request for the same account may change the remembered case.
        CURRENT_CASE_ID.set(case_id)
        return created

    def select_case(self, case_id: Optional[str], required: bool = True) -> Any:
        session_id = current_session_id()
        with self._lock:
            session = self._session_unlocked(session_id)
            resolved = case_id or session.current_case_id
            if not resolved:
                if required:
                    raise KeyError("No active case")
                return self._template
            record = session.cases.get(resolved)
            if record is None:
                raise KeyError(resolved)
            session.current_case_id = resolved
            session.cases.move_to_end(resolved)
            record.last_accessed_at = time.time()
        CURRENT_CASE_ID.set(resolved)
        return record.model

    def current_model(self) -> Any:
        session_id = current_session_id()
        requested_case = current_case_id()
        with self._lock:
            session = self._session_unlocked(session_id)
            resolved = requested_case or session.current_case_id
            if resolved and resolved in session.cases:
                record = session.cases[resolved]
                record.last_accessed_at = time.time()
                return record.model
        return self._template

    def current_case(self) -> Optional[str]:
        requested = current_case_id()
        if requested:
            return requested
        with self._lock:
            return self._session_unlocked(current_session_id()).current_case_id

    def delete_case(self, case_id: str) -> None:
        with self._lock:
            session = self._session_unlocked(current_session_id())
            session.cases.pop(case_id, None)
            if session.current_case_id == case_id:
                session.current_case_id = None

    def expired_session_ids(self) -> list[str]:
        cutoff = time.time() - self._ttl_seconds
        expired: list[str] = []
        with self._lock:
            for session_id, record in list(self._sessions.items()):
                if session_id != "local" and record.last_accessed_at < cutoff:
                    expired.append(session_id)
                    del self._sessions[session_id]
        return expired

    def stats(self) -> dict:
        with self._lock:
            return {
                "sessions": len(self._sessions),
                "cases": sum(len(record.cases) for record in self._sessions.values()),
            }


class ModelProxy:
    def __init__(self, registry: CaseRegistry):
        object.__setattr__(self, "_registry", registry)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry.current_model(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._registry.current_model(), name, value)
