from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Optional


ENTRY_KINDS = {"rgb", "dr", "video", "clinical", "checkin"}
ENTRY_STATUSES = {"completed", "prepared", "manual", "no_measurement"}
PROFILE_SEX_OPTIONS = {"female", "male", "other", "undisclosed"}
MAX_ENTRY_PAYLOAD_BYTES = 32 * 1024


class PersonalCaseValidationError(ValueError):
    """A validation error that is safe to show in the personal-case UI."""


@dataclass(frozen=True)
class PersonalCaseProfile:
    user_id: str
    display_name: str
    birth_date: Optional[str]
    sex: str
    height_cm: Optional[float]
    weight_kg: Optional[float]
    clinician_note: str
    updated_at: Optional[int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "birth_date": self.birth_date,
            "sex": self.sex,
            "height_cm": self.height_cm,
            "weight_kg": self.weight_kg,
            "clinician_note": self.clinician_note,
            "updated_at": self.updated_at,
        }


class PersonalCaseStore:
    """Local SQLite storage for one personal longitudinal case per account."""

    def __init__(self, database_path: Path | str):
        self._memory_connection: Optional[sqlite3.Connection] = None
        if str(database_path) == ":memory:":
            self.database_path = Path(":memory:")
            self._memory_connection = sqlite3.connect(":memory:", timeout=10)
            self._memory_connection.row_factory = sqlite3.Row
        else:
            self.database_path = Path(database_path)
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = self._memory_connection or sqlite3.connect(str(self.database_path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            if connection is not self._memory_connection:
                connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS personal_case_profiles (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    birth_date TEXT,
                    sex TEXT NOT NULL DEFAULT 'undisclosed',
                    height_cm REAL,
                    weight_kg REAL,
                    clinician_note TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS personal_case_entries (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source_filename TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_personal_case_entries_user_date "
                "ON personal_case_entries (user_id, recorded_at DESC, created_at DESC)"
            )
            columns = {row[1] for row in connection.execute('PRAGMA table_info(personal_case_entries)')}
            if 'case_id' not in columns:
                connection.execute('ALTER TABLE personal_case_entries ADD COLUMN case_id TEXT')
            connection.execute('CREATE INDEX IF NOT EXISTS idx_personal_entries_case ON personal_case_entries(user_id, case_id)')

    @staticmethod
    def _clean_text(value: Any, label: str, max_length: int) -> str:
        text = str(value or "").strip()
        if len(text) > max_length:
            raise PersonalCaseValidationError(f"{label}不能超过 {max_length} 个字符")
        return text

    @staticmethod
    def _optional_number(value: Any, label: str, minimum: float, maximum: float) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise PersonalCaseValidationError(f"{label}应为数字") from error
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise PersonalCaseValidationError(f"{label}应在 {minimum:g} 到 {maximum:g} 之间")
        return round(number, 2)

    @staticmethod
    def _optional_date(value: Any) -> Optional[str]:
        if value is None or value == "":
            return None
        try:
            parsed = date.fromisoformat(str(value))
        except (TypeError, ValueError) as error:
            raise PersonalCaseValidationError("日期格式应为 YYYY-MM-DD") from error
        if parsed > date.today():
            raise PersonalCaseValidationError("日期不能晚于今天")
        return parsed.isoformat()

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise PersonalCaseValidationError("记录数据格式无效")
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise PersonalCaseValidationError("记录数据包含无法保存的内容") from error
        if len(encoded.encode("utf-8")) > MAX_ENTRY_PAYLOAD_BYTES:
            raise PersonalCaseValidationError("单条记录数据过大")
        return json.loads(encoded)

    @staticmethod
    def _profile(row: sqlite3.Row) -> PersonalCaseProfile:
        return PersonalCaseProfile(
            user_id=str(row["user_id"]),
            display_name=str(row["display_name"] or ""),
            birth_date=str(row["birth_date"]) if row["birth_date"] else None,
            sex=str(row["sex"] or "undisclosed"),
            height_cm=float(row["height_cm"]) if row["height_cm"] is not None else None,
            weight_kg=float(row["weight_kg"]) if row["weight_kg"] is not None else None,
            clinician_note=str(row["clinician_note"] or ""),
            updated_at=int(row["updated_at"]) if row["updated_at"] is not None else None,
        )

    def get_profile(self, user_id: str) -> Optional[PersonalCaseProfile]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM personal_case_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._profile(row) if row else None

    def update_profile(self, user_id: str, profile: dict[str, Any]) -> PersonalCaseProfile:
        display_name = self._clean_text(profile.get("display_name"), "姓名", 48)
        birth_date = self._optional_date(profile.get("birth_date"))
        sex = str(profile.get("sex") or "undisclosed").strip().lower()
        if sex not in PROFILE_SEX_OPTIONS:
            raise PersonalCaseValidationError("性别选项无效")
        height_cm = self._optional_number(profile.get("height_cm"), "身高", 50, 250)
        weight_kg = self._optional_number(profile.get("weight_kg"), "体重", 10, 400)
        clinician_note = self._clean_text(profile.get("clinician_note"), "病史备注", 2000)
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO personal_case_profiles (
                    user_id, display_name, birth_date, sex, height_cm, weight_kg, clinician_note, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    birth_date = excluded.birth_date,
                    sex = excluded.sex,
                    height_cm = excluded.height_cm,
                    weight_kg = excluded.weight_kg,
                    clinician_note = excluded.clinician_note,
                    updated_at = excluded.updated_at
                """,
                (user_id, display_name, birth_date, sex, height_cm, weight_kg, clinician_note, now),
            )
        return self.get_profile(user_id)  # type: ignore[return-value]

    def create_entry(self, user_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        kind = str(entry.get("kind") or "").strip().lower()
        status = str(entry.get("status") or "completed").strip().lower()
        if kind not in ENTRY_KINDS:
            raise PersonalCaseValidationError("记录类型无效")
        if status not in ENTRY_STATUSES:
            raise PersonalCaseValidationError("记录状态无效")
        recorded_at = self._optional_date(entry.get("recorded_at")) or date.today().isoformat()
        title = self._clean_text(entry.get("title"), "记录标题", 80) or self._default_title(kind)
        summary = self._clean_text(entry.get("summary"), "记录摘要", 600)
        source_filename = self._clean_text(entry.get("source_filename"), "文件名", 255)
        payload = self._payload(entry.get("payload"))
        case_id = self._clean_text(entry.get('case_id'), '检查编号', 80) or None
        entry_id = uuid.uuid4().hex
        created_at = int(time.time())
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            asset_id = payload.get('image_id') if kind in {'dr', 'rgb'} else None
            if case_id and isinstance(asset_id, str) and asset_id:
                existing = connection.execute(
                    "SELECT id FROM personal_case_entries WHERE user_id=? AND case_id=? AND kind=? AND json_extract(payload_json, '$.image_id')=? LIMIT 1",
                    (user_id, case_id, kind, asset_id),
                ).fetchone()
                if existing:
                    entry_id = existing['id']
            connection.execute(
                """
                INSERT INTO personal_case_entries (
                    id, user_id, kind, status, recorded_at, title, summary, source_filename, payload_json, created_at, case_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status, recorded_at=excluded.recorded_at, title=excluded.title,
                    summary=excluded.summary, source_filename=excluded.source_filename,
                    payload_json=excluded.payload_json
                """,
                (entry_id, user_id, kind, status, recorded_at, title, summary, source_filename, payload_json, created_at, case_id),
            )
        return self.get_entry(user_id, entry_id)  # type: ignore[return-value]

    @staticmethod
    def _default_title(kind: str) -> str:
        return {
            "rgb": "RGB 个人检测",
            "dr": "DR / X 光测算",
            "video": "步态视频检测",
            "clinical": "临床复查记录",
            "checkin": "个人状态打卡",
        }[kind]

    @staticmethod
    def _entry(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        return {
            "id": str(row["id"]),
            "case_id": row['case_id'],
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "recorded_at": str(row["recorded_at"]),
            "title": str(row["title"]),
            "summary": str(row["summary"]),
            "source_filename": str(row["source_filename"] or ""),
            "payload": payload,
            "created_at": int(row["created_at"]),
        }

    def get_entry(self, user_id: str, entry_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM personal_case_entries WHERE id = ? AND user_id = ?", (entry_id, user_id)
            ).fetchone()
        return self._entry(row) if row else None

    def list_entries(self, user_id: str, limit: int = 120, *, case_id: str | None = None, include_unassigned: bool = False) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        condition = ''
        parameters: list[Any] = [user_id]
        if case_id is not None:
            condition = ' AND (case_id = ? OR case_id IS NULL)' if include_unassigned else ' AND case_id = ?'
            parameters.append(case_id)
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM personal_case_entries
                WHERE user_id = ? {condition}
                ORDER BY recorded_at DESC, created_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._entry(row) for row in rows]

    def delete_entry(self, user_id: str, entry_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM personal_case_entries WHERE id = ? AND user_id = ?", (entry_id, user_id)
            )
        return cursor.rowcount > 0

    def get_case(self, user_id: str, default_name: str = "", limit: int = 120, *, case_id: str | None = None, include_unassigned: bool = False) -> dict[str, Any]:
        profile = self.get_profile(user_id)
        profile_data = profile.as_dict() if profile else {
            "display_name": default_name,
            "birth_date": None,
            "sex": "undisclosed",
            "height_cm": None,
            "weight_kg": None,
            "clinician_note": "",
            "updated_at": None,
        }
        entries = self.list_entries(user_id, limit=limit, case_id=case_id, include_unassigned=include_unassigned)
        return {
            "profile": profile_data,
            "entries": entries,
            "overview": self._overview(entries),
            "trends": self._trends(entries),
        }

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _trends(self, entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        definitions = {
            "dr_ll": ("DR · LL", lambda p: p.get("angles", {}).get("LL")),
            "dr_ss": ("DR · SS / SA", lambda p: p.get("angles", {}).get("SSA", p.get("angles", {}).get("SS"))),
            "dr_lca": ("DR · LCA", lambda p: p.get("angles", {}).get("HARRISON_A", p.get("angles", {}).get("LCA"))),
            "dr_lci": ("DR · LCI", lambda p: p.get("angles", {}).get("LCI")),
            "clinical_cobb": ("临床 · Cobb 角", lambda p: p.get("cobb_angle")),
            "clinical_atr": ("临床 · 躯干旋转角", lambda p: p.get("atr_angle")),
            "checkin_pain": ("状态 · 疼痛评分", lambda p: p.get("pain_score")),
            "video_positive": ("视频 · 阳性概率", lambda p: p.get("positive_probability")),
        }
        output: dict[str, list[dict[str, Any]]] = {key: [] for key in definitions}
        for entry in reversed(entries):
            for key, (_, extractor) in definitions.items():
                value = self._number(extractor(entry["payload"]))
                if value is not None:
                    output[key].append({"date": entry["recorded_at"], "value": value, "entry_id": entry["id"]})
        return {key: values for key, values in output.items() if values}

    def _overview(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {kind: 0 for kind in ENTRY_KINDS}
        for entry in entries:
            counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
        return {
            "total_entries": len(entries),
            "counts": counts,
            "latest_entry": entries[0] if entries else None,
        }
