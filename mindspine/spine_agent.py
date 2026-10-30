"""Case-centred, local-first spine-health agent primitives.

This module deliberately contains deterministic storage, triage and tool-policy
logic.  A language model may improve wording, but it cannot grant permission,
change a safety decision, invent a tool call, or write arbitrary data.
"""

from __future__ import annotations

import hashlib

import hmac
import json
import math
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


CASE_ROLES = {"owner", "patient", "clinician", "researcher"}
ACTION_STATES = {
    "draft",
    "confirmed",
    "queued",
    "running",
    "completed",
    "needs_review",
    "failed",
    "cancelled",
}
ASSESSMENT_KINDS = {
    "video_screening",
    "surface_archive",
    "ctmr_segmentation",
    "multicam_kinematics",
}
MAX_JSON_BYTES = 96 * 1024
MAX_MESSAGE_LENGTH = 1600
_CASE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_ACTION_TOKEN_TTL_SECONDS = 15 * 60


class SpineAgentValidationError(ValueError):
    """A validation error that is safe to render in the workbench."""


class SpineAgentPermissionError(PermissionError):
    """Raised when a user attempts to access an unshared case."""


@dataclass(frozen=True)
class ToolSpec:
    id: str
    version: str
    input_schema: str
    output_schema: str
    case_scope: str
    raw_data_access: bool
    approval_level: str
    timeout_seconds: int
    retry_policy: str
    validator: str
    fallback: str
    enabled: bool
    reproduction_status: str
    upstream_project: str
    upstream_license: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "read_case_summary": ToolSpec(
        id="read_case_summary",
        version="1.0.0",
        input_schema="{case_id}",
        output_schema="CaseSummary",
        case_scope="read",
        raw_data_access=False,
        approval_level="automatic",
        timeout_seconds=3,
        retry_policy="none",
        validator="case_membership_and_consent",
        fallback="deny",
        enabled=True,
        reproduction_status="implemented",
        upstream_project="internal",
        upstream_license="proprietary",
    ),
    "retrieve_clinical_evidence": ToolSpec(
        id="retrieve_clinical_evidence",
        version="1.0.0",
        input_schema="{query, audience, age_group}",
        output_schema="Evidence[]",
        case_scope="read",
        raw_data_access=False,
        approval_level="automatic",
        timeout_seconds=8,
        retry_policy="one_safe_retry",
        validator="approved_source_and_audience_filter",
        fallback="abstain",
        enabled=True,
        reproduction_status="implemented_lexical_baseline",
        upstream_project="MedRAG-inspired",
        upstream_license="NCBI public-domain reference",
    ),
    "run_video_screening": ToolSpec(
        id="run_video_screening",
        version="0.1.0",
        input_schema="{case_id, asset_id, protocol_version}",
        output_schema="VideoAssessment",
        case_scope="write",
        raw_data_access=True,
        approval_level="explicit_confirmation",
        timeout_seconds=180,
        retry_policy="idempotent_safe_retry",
        validator="asset_owner_protocol_quality",
        fallback="needs_review",
        enabled=False,
        reproduction_status="verified_algorithmic_core: RepDetect 33-landmark embedding, two-stage KNN and confidence-hysteresis counter passed fixed synthetic traces; Android/ML Kit real-video Worker remains disabled",
        upstream_project="giaongo/RepDetect",
        upstream_license="Apache-2.0",
    ),
    "build_surface_archive": ToolSpec(
        id="build_surface_archive",
        version="0.1.0",
        input_schema="{case_id, asset_ids, capture_protocol_version}",
        output_schema="SurfaceArchive",
        case_scope="write",
        raw_data_access=True,
        approval_level="explicit_confirmation",
        timeout_seconds=1800,
        retry_policy="idempotent_safe_retry",
        validator="asset_owner_capture_quality",
        fallback="needs_review",
        enabled=False,
        reproduction_status="adapter_contract_ready; COLMAP/Open3D worker pending",
        upstream_project="COLMAP + Open3D",
        upstream_license="BSD + MIT",
    ),
    "run_ctmr_segmentation": ToolSpec(
        id="run_ctmr_segmentation",
        version="0.1.0",
        input_schema="{case_id, asset_id, modality}",
        output_schema="SegmentationAssessment",
        case_scope="write",
        raw_data_access=True,
        approval_level="explicit_confirmation",
        timeout_seconds=3600,
        retry_policy="none",
        validator="deidentified_dicom_nifti_and_worker_health",
        fallback="needs_review",
        enabled=False,
        reproduction_status="adapter_contract_ready; TotalSegmentator/MONAI worker pending",
        upstream_project="wasserth/TotalSegmentator + Project-MONAI/MONAI",
        upstream_license="Apache-2.0",
    ),
    "run_multicam_kinematics": ToolSpec(
        id="run_multicam_kinematics",
        version="0.1.0",
        input_schema="{case_id, asset_ids, calibration_id}",
        output_schema="KinematicsAssessment",
        case_scope="write",
        raw_data_access=True,
        approval_level="explicit_confirmation",
        timeout_seconds=5400,
        retry_policy="none",
        validator="camera_sync_calibration_and_worker_health",
        fallback="needs_review",
        enabled=False,
        reproduction_status="verified_algorithmic_core: calibrated multi-view DLT, synchronization and reprojection gates passed fixed synthetic fixtures; OpenSim Worker remains disabled",
        upstream_project="opencap-org/opencap-core",
        upstream_license="Apache-2.0",
    ),
}


THIRD_PARTY_REPRODUCTION_PLAN = (
    {
        "id": "repdetect",
        "project": "giaongo/RepDetect",
        "license": "Apache-2.0",
        "adapter": "run_video_screening",
        "gate": "isolate Android sample; lock commit and dependencies; validate synthetic pose trace; then validate Python adapter contract",
        "status": "algorithmic_core_verified_product_worker_disabled",
    },
    {
        "id": "opencap_core",
        "project": "opencap-org/opencap-core",
        "license": "Apache-2.0",
        "adapter": "run_multicam_kinematics",
        "gate": "container health check, synchronized multi-camera calibration fixture, OpenSim output schema and cancellation test",
        "status": "algorithmic_core_verified_product_worker_disabled",
    },
    {
        "id": "totalsegmentator_monai",
        "project": "wasserth/TotalSegmentator + Project-MONAI/MONAI",
        "license": "Apache-2.0",
        "adapter": "run_ctmr_segmentation",
        "gate": "deidentified DICOM/NIfTI fixture, model card review, output provenance and manual-review route",
        "status": "pending_reproduction",
    },
    {
        "id": "multimodal_torso",
        "project": "luantran/MultimodalTorsoImageReconstruction",
        "license": "MIT",
        "adapter": "build_surface_archive",
        "gate": "only non-diagnostic surface archive in v1; professional registration requires same-session calibration and expert review",
        "status": "reference_only_until_calibrated",
    },
    {
        "id": "medrag",
        "project": "gzxiong/MedRAG",
        "license": "NCBI public-domain notice",
        "adapter": "retrieve_clinical_evidence",
        "gate": "fixed commit, approved local corpus, retrieval golden set and citation coverage test",
        "status": "lexical_baseline_implemented; semantic_worker_pending",
    },
    {
        "id": "multi_agent_medical_assistant",
        "project": "souvikmajumder26/Multi-Agent-Medical-Assistant",
        "license": "Apache-2.0",
        "adapter": "agent_orchestration_and_ingestion_reference",
        "gate": "reuse only compatible FastAPI/RAG patterns; no unrelated image models or web-diagnosis paths",
        "status": "architecture_reference",
    },
)


def capture_quality(capture: Any, landmarks: Any, protocol: Any) -> dict[str, Any]:
    """Return the conservative minimum quality used for modality eligibility."""

    values: list[float] = []
    for name, value in (("capture", capture), ("landmarks", landmarks), ("protocol", protocol)):
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise SpineAgentValidationError(f"{name} 质量分数无效") from error
        if not math.isfinite(number) or not 0 <= number <= 1:
            raise SpineAgentValidationError(f"{name} 质量分数应在 0 到 1 之间")
        values.append(number)
    score = round(min(values), 4)
    return {"score": score, "eligible": score >= 0.7, "components": dict(zip(("capture", "landmarks", "protocol"), values))}


def measurements_comparable(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Reject invalid trend comparisons instead of guessing a normalization."""

    required = ("protocol_version", "model_version", "unit", "capture_condition")
    mismatches = [field for field in required if previous.get(field) != current.get(field)]
    old_quality = previous.get("quality", {}).get("score", previous.get("quality_score"))
    new_quality = current.get("quality", {}).get("score", current.get("quality_score"))
    try:
        quality_ok = float(old_quality) >= 0.7 and float(new_quality) >= 0.7
    except (TypeError, ValueError):
        quality_ok = False
    if not quality_ok:
        mismatches.append("quality")
    return {"comparable": not mismatches, "reasons": mismatches}


def chunk_knowledge_sections(sections: Iterable[dict[str, Any]], target_characters: int = 600, overlap_characters: int = 60) -> list[dict[str, Any]]:
    """Create audit-friendly chunks without splitting decision tables/entities.

    Callers provide already parsed, heading-aware sections.  Guideline prose is
    split near paragraph boundaries at about 400–700 Chinese characters; tables,
    questionnaire items and exercise definitions stay atomic so contraindication
    rows cannot become separated from their action.
    """

    target = max(400, min(int(target_characters), 700))
    overlap = max(0, min(int(overlap_characters), 120))
    chunks: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            raise SpineAgentValidationError("知识分段格式无效")
        heading = str(section.get("heading") or "未命名章节").strip()[:160]
        kind = str(section.get("kind") or "prose").strip().lower()
        content = str(section.get("content") or "").strip()
        if kind not in {"prose", "table", "paper_abstract", "exercise", "questionnaire"} or not content:
            raise SpineAgentValidationError("知识分段缺少有效的类型或内容")
        metadata = section.get("metadata") if isinstance(section.get("metadata"), dict) else {}
        if kind in {"table", "exercise", "questionnaire"}:
            if len(content) > 12_000:
                raise SpineAgentValidationError("结构化知识单元过大，应在入库前按独立决策单元拆分")
            chunks.append({"heading": heading, "kind": kind, "content": content, "metadata": metadata})
            continue
        paragraphs = [piece.strip() for piece in re.split(r"\n\s*\n", content) if piece.strip()]
        buffer = ""
        for paragraph in paragraphs or [content]:
            if len(buffer) + len(paragraph) + 1 <= target:
                buffer = f"{buffer}\n{paragraph}".strip()
                continue
            if buffer:
                chunks.append({"heading": heading, "kind": kind, "content": buffer, "metadata": metadata})
            remaining = paragraph
            prefix = buffer[-overlap:] if overlap and buffer else ""
            buffer = ""
            while len(remaining) > target:
                split_at = remaining.rfind("。", max(160, target - 120), target + 1)
                if split_at < 160:
                    cut = target
                else:
                    cut = split_at + 1
                piece = (prefix + remaining[:cut]).strip()
                chunks.append({"heading": heading, "kind": kind, "content": piece, "metadata": metadata})
                prefix = remaining[max(0, cut - overlap):cut] if overlap else ""
                remaining = remaining[cut:].strip()
            buffer = (prefix + remaining).strip()
        if buffer:
            chunks.append({"heading": heading, "kind": kind, "content": buffer, "metadata": metadata})
    return chunks


class MovementStateCounter:
    """A small, deterministic state machine for a reviewed exercise protocol.

    It intentionally consumes an already extracted signal rather than pretending
    to be a medical pose model.  A future RepDetect adapter feeds this state
    machine only after capture/landmark quality has passed.
    """

    def __init__(self, low_threshold: float, high_threshold: float, min_interval_seconds: float = 0.45):
        if not 0 <= low_threshold < high_threshold <= 1:
            raise ValueError("thresholds must satisfy 0 <= low < high <= 1")
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold
        self.min_interval_seconds = max(0.05, float(min_interval_seconds))
        self.state = "high"
        self.repetitions = 0
        self._last_rep_at = float("-inf")

    def observe(self, signal: float, at_seconds: float) -> int:
        if not math.isfinite(signal) or not math.isfinite(at_seconds):
            return self.repetitions
        if self.state == "high" and signal <= self.low_threshold:
            self.state = "low"
        elif self.state == "low" and signal >= self.high_threshold:
            if at_seconds - self._last_rep_at >= self.min_interval_seconds:
                self.repetitions += 1
                self._last_rep_at = at_seconds
            self.state = "high"
        return self.repetitions


class SpineAgentStore:
    """Local SQLite source of truth for cases, consent, actions and evidence."""

    def __init__(self, database_path: Path | str):
        self._memory_connection: Optional[sqlite3.Connection] = None
        if str(database_path) == ":memory:":
            self.database_path = Path(":memory:")
            self._memory_connection = sqlite3.connect(":memory:", timeout=10)
            self._memory_connection.row_factory = sqlite3.Row
        else:
            self.database_path = Path(database_path)
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._fts_enabled = False
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
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS spine_cases (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    care_scope TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_spine_cases_owner ON spine_cases(owner_user_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS case_memberships (
                    case_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    expires_at INTEGER,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(case_id, user_id),
                    FOREIGN KEY(case_id) REFERENCES spine_cases(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS case_consents (
                    case_id TEXT PRIMARY KEY,
                    external_ai_allowed INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES spine_cases(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS questionnaire_responses (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    protocol_version TEXT NOT NULL,
                    answers_json TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    triage_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES spine_cases(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_questionnaire_case ON questionnaire_responses(case_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS assessment_jobs (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    tool_version TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES spine_cases(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_assessment_case ON assessment_jobs(case_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS agent_threads (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES spine_cases(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS agent_messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    audit_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES agent_threads(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_messages_thread ON agent_messages(thread_id, created_at ASC);
                CREATE TABLE IF NOT EXISTS agent_actions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    thread_id TEXT,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    confirmation_hash TEXT NOT NULL,
                    confirmation_expires_at INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_by TEXT NOT NULL,
                    confirmed_by TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES spine_cases(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_actions_case ON agent_actions(case_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    usage_policy TEXT NOT NULL,
                    published_at TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    heading TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document ON knowledge_chunks(document_id);
                CREATE TABLE IF NOT EXISTS exercise_catalog (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rehab_plans (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    source_catalog_version TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES spine_cases(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS review_tasks (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    state TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    resolved_at INTEGER,
                    resolved_by TEXT,
                    FOREIGN KEY(case_id) REFERENCES spine_cases(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_review_tasks_case ON review_tasks(case_id, state, created_at DESC);
                """
            )
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(chunk_id UNINDEXED, content)"
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False
        self._seed_reference_knowledge()

    @staticmethod
    def _now() -> int:
        return int(time.time())

    @staticmethod
    def _id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _clean_text(value: Any, label: str, maximum: int, required: bool = False) -> str:
        text = str(value or "").replace("\x00", "").strip()
        if required and not text:
            raise SpineAgentValidationError(f"{label}不能为空")
        if len(text) > maximum:
            raise SpineAgentValidationError(f"{label}不能超过 {maximum} 个字符")
        return text

    @staticmethod
    def _json(value: Any, label: str = "数据") -> str:
        try:
            encoded = json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise SpineAgentValidationError(f"{label}包含无法保存的内容") from error
        if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
            raise SpineAgentValidationError(f"{label}过大")
        return encoded

    @staticmethod
    def _load_json(value: Any, default: Any) -> Any:
        try:
            result = json.loads(str(value or ""))
            return result
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    @staticmethod
    def _row_to_case(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "owner_user_id": str(row["owner_user_id"]),
            "title": str(row["title"]),
            "care_scope": str(row["care_scope"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def _seed_reference_knowledge(self) -> None:
        """Store metadata and operational boundaries, never copied guideline text."""

        references = (
            {
                "id": "who-cplbp-2023-reference",
                "title": "WHO：成人慢性原发性腰痛非手术干预指南（待临床审核参考）",
                "url": "https://www.who.int/publications/i/item/9789240081789",
                "topic": "adult_low_back_health",
                "content": "该来源已登记为成人慢性原发性腰痛的权威候选参考。未经本产品临床审核签署前，不能据此自动生成个体化动作处方。",
            },
            {
                "id": "nice-ng59-reference",
                "title": "NICE NG59：腰痛与坐骨神经痛指南（待临床审核参考）",
                "url": "https://www.nice.org.uk/guidance/ng59/",
                "topic": "adult_low_back_health",
                "content": "该来源已登记为成人腰痛与坐骨神经痛的权威候选参考。知识使用前需要明确适用人群、版本和临床审核状态。",
            },
            {
                "id": "spine-agent-safety-boundary",
                "title": "MindSpine Agent 安全边界",
                "url": "internal://spine-agent/safety-boundary/v1",
                "topic": "safety",
                "content": "系统仅用于辅助随访、资料整理和健康教育。出现新的大小便功能改变、会阴区麻木、进行性无力或其他严重症状时，不执行自动康复计划，应转为线下紧急评估提示。",
            },
        )
        with self._connect() as connection:
            for reference in references:
                existing = connection.execute("SELECT id FROM knowledge_documents WHERE id = ?", (reference["id"],)).fetchone()
                if existing:
                    continue
                now = self._now()
                review_status = "operational" if reference["id"] == "spine-agent-safety-boundary" else "reference_pending_clinical_review"
                usage_policy = "operational_safety" if review_status == "operational" else "reference_only"
                connection.execute(
                    """
                    INSERT INTO knowledge_documents(id, title, source_url, audience, topic, review_status, usage_policy, created_at, updated_at)
                    VALUES (?, ?, ?, 'adult', ?, ?, ?, ?, ?)
                    """,
                    (reference["id"], reference["title"], reference["url"], reference["topic"], review_status, usage_policy, now, now),
                )
                chunk_id = self._id()
                connection.execute(
                    "INSERT INTO knowledge_chunks(id, document_id, heading, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (chunk_id, reference["id"], "摘要", reference["content"], self._json({"kind": "reference"}), now),
                )
                if self._fts_enabled:
                    connection.execute("INSERT INTO knowledge_chunks_fts(chunk_id, content) VALUES (?, ?)", (chunk_id, reference["content"]))

    def register_knowledge_document(
        self,
        document: dict[str, Any],
        sections: Iterable[dict[str, Any]],
        *,
        reviewed_by: str = "",
    ) -> dict[str, Any]:
        """Ingest a rights-checked document after an operator/clinical review.

        This is deliberately an internal service API, not a patient-facing
        upload endpoint.  A caller must retain source rights and record the
        reviewer before setting a clinical document to ``approved``.
        """

        if not isinstance(document, dict):
            raise SpineAgentValidationError("知识文档格式无效")
        title = self._clean_text(document.get("title"), "文档标题", 240, required=True)
        source_url = self._clean_text(document.get("source_url"), "来源链接", 1000, required=True)
        if not source_url.startswith(("https://", "internal://")):
            raise SpineAgentValidationError("来源链接仅支持 https 或 internal 地址")
        audience = self._clean_text(document.get("audience", "adult"), "适用人群", 64, required=True)
        topic = self._clean_text(document.get("topic"), "知识主题", 80, required=True)
        review_status = self._clean_text(document.get("review_status", "draft"), "审核状态", 64, required=True)
        usage_policy = self._clean_text(document.get("usage_policy", "reference_only"), "使用策略", 64, required=True)
        if review_status not in {"draft", "reference_pending_clinical_review", "approved", "operational", "retired"}:
            raise SpineAgentValidationError("知识审核状态无效")
        if review_status == "approved" and not self._clean_text(reviewed_by, "临床审核人", 120):
            raise SpineAgentValidationError("临床知识发布前必须记录审核人")
        chunks = chunk_knowledge_sections(sections)
        if not chunks:
            raise SpineAgentValidationError("知识文档至少需要一个有效分段")
        now = self._now()
        document_id = self._id()
        published_at = self._clean_text(document.get("published_at"), "发布日期", 32)
        expires_at = self._clean_text(document.get("expires_at"), "失效日期", 32)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_documents(id, title, source_url, audience, topic, review_status, usage_policy, published_at, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (document_id, title, source_url, audience, topic, review_status, usage_policy, published_at, expires_at, now, now),
            )
            for chunk in chunks:
                chunk_id = self._id()
                metadata = {"kind": chunk["kind"], "parent_heading": chunk["heading"], "reviewed_by": reviewed_by, **chunk["metadata"]}
                connection.execute(
                    "INSERT INTO knowledge_chunks(id, document_id, heading, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (chunk_id, document_id, chunk["heading"], chunk["content"], self._json(metadata), now),
                )
                if self._fts_enabled:
                    connection.execute("INSERT INTO knowledge_chunks_fts(chunk_id, content) VALUES (?, ?)", (chunk_id, chunk["content"]))
        return {"id": document_id, "title": title, "review_status": review_status, "usage_policy": usage_policy, "chunk_count": len(chunks)}

    def upsert_exercise_catalog_item(self, item: dict[str, Any], *, reviewed_by: str = "") -> dict[str, Any]:
        """Store a versioned exercise definition; only approved rows are eligible."""

        if not isinstance(item, dict):
            raise SpineAgentValidationError("动作目录格式无效")
        exercise_id = self._clean_text(item.get("id"), "动作标识", 96, required=True)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,95}", exercise_id):
            raise SpineAgentValidationError("动作标识只能使用小写字母、数字、下划线或连字符")
        title = self._clean_text(item.get("title"), "动作名称", 120, required=True)
        audience = self._clean_text(item.get("audience", "adult"), "适用人群", 64, required=True)
        review_status = self._clean_text(item.get("review_status", "draft"), "审核状态", 32, required=True)
        if audience != "adult" or review_status not in {"draft", "approved", "retired"}:
            raise SpineAgentValidationError("动作目录仅接受成人范围的 draft、approved 或 retired 状态")
        definition = item.get("definition")
        if not isinstance(definition, dict):
            raise SpineAgentValidationError("动作定义必须是结构化对象")
        required = {"eligibility", "contraindications", "stop_conditions", "dose_bounds", "version"}
        if not required.issubset(definition):
            raise SpineAgentValidationError("动作定义缺少适用条件、禁忌、停止条件、剂量边界或版本")
        if review_status == "approved" and not self._clean_text(reviewed_by, "临床审核人", 120):
            raise SpineAgentValidationError("动作发布前必须记录临床审核人")
        now = self._now()
        payload = {**definition, "reviewed_by": reviewed_by}
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO exercise_catalog(id, title, audience, review_status, data_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title, audience=excluded.audience, review_status=excluded.review_status,
                    data_json=excluded.data_json, updated_at=excluded.updated_at
                """,
                (exercise_id, title, audience, review_status, self._json(payload), now),
            )
        return {"id": exercise_id, "title": title, "audience": audience, "review_status": review_status, "version": definition["version"], "updated_at": now}

    def exercise_catalog_status(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT review_status, COUNT(*) AS count FROM exercise_catalog WHERE audience = 'adult' GROUP BY review_status").fetchall()
        counts = {str(row["review_status"]): int(row["count"]) for row in rows}
        return {
            "counts": counts,
            "can_generate_plan": counts.get("approved", 0) > 0,
            "rule": "只有 approved 的成人动作定义可参与计划；Agent 不得自行创建动作、剂量或禁忌。",
        }

    def _latest_questionnaire_unchecked(self, connection: sqlite3.Connection, case_id: str) -> Optional[dict[str, Any]]:
        row = connection.execute(
            "SELECT * FROM questionnaire_responses WHERE case_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (case_id,)
        ).fetchone()
        return self._questionnaire_row(row)

    def _create_rehab_plan_unchecked(self, connection: sqlite3.Connection, case_id: str, user_id: str) -> dict[str, Any]:
        questionnaire = self._latest_questionnaire_unchecked(connection, case_id)
        triage = questionnaire.get("triage", {}) if questionnaire else {}
        if triage.get("risk_level") != "routine" or triage.get("automation_allowed") is not True:
            raise SpineAgentValidationError("当前分流状态不允许生成自动康复计划")
        trigger_ids = {str(item.get("id")) for item in triage.get("triggers", []) if isinstance(item, dict)}
        rows = connection.execute(
            "SELECT * FROM exercise_catalog WHERE audience = 'adult' AND review_status = 'approved' ORDER BY id ASC"
        ).fetchall()
        selected = []
        for row in rows:
            definition = self._load_json(row["data_json"], {})
            eligibility = definition.get("eligibility") if isinstance(definition.get("eligibility"), dict) else {}
            allowed_risks = eligibility.get("risk_levels", ["routine"])
            contraindications = set(definition.get("contraindications", [])) if isinstance(definition.get("contraindications"), list) else set()
            if "routine" not in allowed_risks or trigger_ids.intersection(map(str, contraindications)):
                continue
            selected.append(
                {
                    "exercise_id": str(row["id"]),
                    "title": str(row["title"]),
                    "version": str(definition.get("version")),
                    "dose_bounds": definition.get("dose_bounds"),
                    "stop_conditions": definition.get("stop_conditions"),
                }
            )
            if len(selected) >= 3:
                break
        if not selected:
            raise SpineAgentValidationError("当前审核动作目录中没有与该分流状态匹配的计划模板")
        now = self._now()
        plan_id = self._id()
        source_version = ",".join(f"{item['exercise_id']}@{item['version']}" for item in selected)
        plan = {
            "mode": "catalog_constrained",
            "questionnaire_id": questionnaire["id"],
            "risk_level": "routine",
            "items": selected,
            "safety_note": "如出现停止条件或症状变化，请立即停止并转为线下复核。",
        }
        connection.execute(
            "INSERT INTO rehab_plans(id, case_id, state, source_catalog_version, plan_json, created_by, created_at) VALUES (?, ?, 'active', ?, ?, ?, ?)",
            (plan_id, case_id, source_version, self._json(plan), user_id, now),
        )
        return {"id": plan_id, "state": "active", "source_catalog_version": source_version, "plan": plan, "created_at": now}

    def list_rehab_plans(self, user_id: str, case_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id)
            rows = connection.execute(
                "SELECT * FROM rehab_plans WHERE case_id = ? ORDER BY created_at DESC LIMIT ?", (case_id, max(1, min(int(limit), 50)))
            ).fetchall()
        return [
            {"id": str(row["id"]), "state": str(row["state"]), "source_catalog_version": str(row["source_catalog_version"]),
             "plan": self._load_json(row["plan_json"], {}), "created_by": str(row["created_by"]), "created_at": int(row["created_at"])}
            for row in rows
        ]

    def ensure_default_case(self, user_id: str, title: str = "我的脊柱健康档案") -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM spine_cases WHERE owner_user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (user_id,)
            ).fetchone()
            if row:
                return self._row_to_case(row)
        return self.create_case(user_id, title)

    def create_case(self, user_id: str, title: str, care_scope: str = "spine_exam") -> dict[str, Any]:
        clean_title = self._clean_text(title, "病例名称", 80, required=True)
        clean_scope = self._clean_text(care_scope, "病例范围", 64, required=True)
        now = self._now()
        case_id = self._id()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO spine_cases(id, owner_user_id, title, care_scope, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (case_id, user_id, clean_title, clean_scope, now, now),
            )
            connection.execute(
                "INSERT INTO case_memberships(case_id, user_id, role, scopes_json, expires_at, created_at) VALUES (?, ?, 'owner', ?, NULL, ?)",
                (case_id, user_id, self._json(["read", "write", "share", "review", "export"]), now),
            )
            connection.execute(
                "INSERT INTO case_consents(case_id, external_ai_allowed, updated_at) VALUES (?, 0, ?)", (case_id, now)
            )
        return self.get_case(user_id, case_id)

    def is_legacy_case(self, user_id: str, case_id: str) -> bool:
        """Legacy one-person entries belong only to the original owned ledger."""
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id)
            row = connection.execute(
                "SELECT id FROM spine_cases WHERE owner_user_id=? ORDER BY created_at ASC, rowid ASC LIMIT 1", (user_id,)
            ).fetchone()
        return bool(row and row['id'] == case_id)

    def require_write(self, user_id: str, case_id: str):
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id, write=True)

    def update_action_result(self, user_id: str, case_id: str, action_id: str, state: str, result: dict):
        if state not in ACTION_STATES:
            raise SpineAgentValidationError('任务状态无效')
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id, write=True)
            connection.execute('UPDATE agent_actions SET state=?,result_json=?,updated_at=? WHERE id=? AND case_id=?',
                               (state, self._json(result), self._now(), action_id, case_id))

    def refresh_confirmation(self, user_id: str, case_id: str, action_id: str):
        token = secrets.token_urlsafe(24)
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id, write=True)
            row = connection.execute('SELECT * FROM agent_actions WHERE id=? AND case_id=?', (action_id, case_id)).fetchone()
            if not row or row['state'] != 'draft' or row['created_by'] != user_id:
                raise SpineAgentPermissionError('此动作不可重新确认')
            expires = self._now() + _ACTION_TOKEN_TTL_SECONDS
            connection.execute('UPDATE agent_actions SET confirmation_hash=?,confirmation_expires_at=? WHERE id=?',
                               (hashlib.sha256(token.encode()).hexdigest(), expires, action_id))
        return {'confirmation_token': token, 'confirmation_expires_at': expires}

    def _membership(self, connection: sqlite3.Connection, user_id: str, case_id: str) -> Optional[sqlite3.Row]:
        return connection.execute(
            """
            SELECT * FROM case_memberships
            WHERE case_id = ? AND user_id = ? AND (expires_at IS NULL OR expires_at > ?)
            """,
            (case_id, user_id, self._now()),
        ).fetchone()

    def _require_access(self, connection: sqlite3.Connection, user_id: str, case_id: str, write: bool = False) -> sqlite3.Row:
        if not _CASE_ID_RE.fullmatch(case_id or ""):
            raise SpineAgentPermissionError("病例标识无效")
        membership = self._membership(connection, user_id, case_id)
        if membership is None:
            raise SpineAgentPermissionError("你没有访问该病例的权限")
        role = str(membership["role"])
        if write and role == "researcher":
            raise SpineAgentPermissionError("研究协作者不能修改病例")
        return membership

    def get_case(self, user_id: str, case_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            membership = self._require_access(connection, user_id, case_id)
            row = connection.execute("SELECT * FROM spine_cases WHERE id = ?", (case_id,)).fetchone()
            if row is None:
                raise SpineAgentPermissionError("病例不存在")
            item = self._row_to_case(row)
            item["role"] = str(membership["role"])
            item["scopes"] = self._load_json(membership["scopes_json"], [])
            return item

    def list_cases(self, user_id: str) -> list[dict[str, Any]]:
        now = self._now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.*, m.role, m.scopes_json, m.expires_at
                FROM spine_cases c JOIN case_memberships m ON c.id = m.case_id
                WHERE m.user_id = ? AND (m.expires_at IS NULL OR m.expires_at > ?)
                ORDER BY c.updated_at DESC
                """,
                (user_id, now),
            ).fetchall()
        result = []
        for row in rows:
            item = self._row_to_case(row)
            item["role"] = str(row["role"])
            item["scopes"] = self._load_json(row["scopes_json"], [])
            item["expires_at"] = int(row["expires_at"]) if row["expires_at"] is not None else None
            result.append(item)
        return result

    def invite_member(self, user_id: str, case_id: str, member_user_id: str, role: str, expires_at: Optional[int]) -> dict[str, Any]:
        role = str(role or "").strip().lower()
        if role not in CASE_ROLES - {"owner"}:
            raise SpineAgentValidationError("协作角色无效")
        if not re.fullmatch(r"[0-9a-f]{32}", member_user_id or ""):
            raise SpineAgentValidationError("协作账号标识无效")
        if expires_at is not None and int(expires_at) <= self._now():
            raise SpineAgentValidationError("授权到期时间必须晚于当前时间")
        scopes = {
            "patient": ["read", "write"],
            "clinician": ["read", "write", "review", "export"],
            "researcher": ["read_deidentified"],
        }[role]
        with self._connect() as connection:
            membership = self._require_access(connection, user_id, case_id, write=True)
            if membership["role"] != "owner":
                raise SpineAgentPermissionError("只有病例所有者可以授予协作权限")
            connection.execute(
                """
                INSERT INTO case_memberships(case_id, user_id, role, scopes_json, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id, user_id) DO UPDATE SET role=excluded.role, scopes_json=excluded.scopes_json, expires_at=excluded.expires_at
                """,
                (case_id, member_user_id, role, self._json(scopes), expires_at, self._now()),
            )
        return {"case_id": case_id, "user_id": member_user_id, "role": role, "scopes": scopes, "expires_at": expires_at}

    def get_consent(self, user_id: str, case_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id)
            row = connection.execute("SELECT * FROM case_consents WHERE case_id = ?", (case_id,)).fetchone()
        return {
            "external_ai_allowed": bool(row["external_ai_allowed"]) if row else False,
            "updated_at": int(row["updated_at"]) if row else None,
            "policy": "联网 Agent 使用当前问题、近期对话、已授权检查摘要和检索到的知识资料；可在此关闭联网回答。",
        }

    def set_consent(self, user_id: str, case_id: str, external_ai_allowed: bool) -> dict[str, Any]:
        with self._connect() as connection:
            membership = self._require_access(connection, user_id, case_id, write=True)
            if membership["role"] not in {"owner", "patient"}:
                raise SpineAgentPermissionError("只有病例本人可以设置外部 AI 同意")
            now = self._now()
            connection.execute(
                """
                INSERT INTO case_consents(case_id, external_ai_allowed, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET external_ai_allowed=excluded.external_ai_allowed, updated_at=excluded.updated_at
                """,
                (case_id, 1 if external_ai_allowed else 0, now),
            )
        return self.get_consent(user_id, case_id)

    @staticmethod
    def _finite_number(value: Any, label: str, minimum: float, maximum: float) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            result = float(value)
        except (TypeError, ValueError) as error:
            raise SpineAgentValidationError(f"{label}应为数字") from error
        if not math.isfinite(result) or not minimum <= result <= maximum:
            raise SpineAgentValidationError(f"{label}应在 {minimum:g} 到 {maximum:g} 之间")
        return result

    def _triage(self, answers: dict[str, Any]) -> dict[str, Any]:
        if answers.get("consent_confirmed", answers.get("adult_confirmed")) is not True:
            raise SpineAgentValidationError("请确认本次填写的信息和使用说明后继续")
        pain = self._finite_number(answers.get("pain_score"), "疼痛评分", 0, 10)
        activity_limit = self._finite_number(answers.get("activity_limit"), "活动受限评分", 0, 10)
        raw_flags = answers.get("red_flags", [])
        if isinstance(raw_flags, dict):
            flags = {str(name) for name, enabled in raw_flags.items() if enabled is True}
        elif isinstance(raw_flags, list):
            flags = {str(item) for item in raw_flags}
        else:
            raise SpineAgentValidationError("红旗信息格式无效")
        supported = {
            "new_bowel_or_bladder_change": (3, "新的大小便功能改变"),
            "saddle_numbness": (3, "会阴区域麻木"),
            "progressive_weakness": (3, "进行性下肢无力"),
            "severe_trauma": (2, "近期严重外伤"),
            "fever_or_infection": (2, "发热或感染相关情况"),
            "cancer_history_with_new_pain": (2, "肿瘤病史伴新发疼痛"),
            "unexplained_weight_loss": (2, "无明显原因的体重下降"),
            "night_pain": (1, "持续夜间痛"),
        }
        unknown = flags - set(supported)
        if unknown:
            raise SpineAgentValidationError("包含无法识别的筛查选项")
        severity = max([supported[flag][0] for flag in flags] + [0])
        if pain is not None and pain >= 7:
            severity = max(severity, 1)
        if activity_limit is not None and activity_limit >= 7:
            severity = max(severity, 1)
        # A missing required symptom score is not evidence of low risk.  Keep
        # explicit red-flag decisions above this state, but never enable
        # automatic rehabilitation from an incomplete intake.
        required_answers_complete = pain is not None and activity_limit is not None
        risk_level = {0: "routine", 1: "attention", 2: "soon", 3: "urgent"}[severity]
        if severity == 0 and not required_answers_complete:
            risk_level = "incomplete"
        triggers = [{"id": flag, "label": supported[flag][1], "severity": supported[flag][0]} for flag in sorted(flags)]
        return {
            "risk_level": risk_level,
            "max_rule_severity": severity,
            "triggers": triggers,
            "pain_score": pain,
            "activity_limit": activity_limit,
            "data_complete": required_answers_complete,
            "automation_allowed": risk_level == "routine",
            "message": {
                "urgent": "筛查发现需要优先线下评估的情况；系统不会继续生成自动训练建议。",
                "soon": "筛查提示应尽快咨询线下专业人员；系统不会生成自动训练建议。",
                "attention": "当前资料提示需要谨慎随访；系统仅提供资料整理与一般性安全提醒。",
                "routine": "未触发本问卷中的高优先级规则；这不等于排除疾病，仍需结合实际症状和专业意见。",
                "incomplete": "尚未完成疼痛与活动受限评分；系统不会将此视为低风险，也不会生成自动训练建议。",
            }[risk_level],
        }

    def submit_questionnaire(self, user_id: str, case_id: str, answers: dict[str, Any], protocol_version: str = "spine-intake-v2") -> dict[str, Any]:
        if not isinstance(answers, dict):
            raise SpineAgentValidationError("问卷答案格式无效")
        protocol_version = self._clean_text(protocol_version, "问卷版本", 64, required=True)
        if protocol_version == "spine-intake-v3":
            from .questionnaire import normalize_answers, triage_answers
            try:
                answers = normalize_answers(answers)
                triage = triage_answers(answers)
            except ValueError as error:
                raise SpineAgentValidationError(str(error)) from error
        elif protocol_version in {"spine-intake-v1", "spine-intake-v2", "adult-spine-intake-v1"}:
            # Existing v1/v2 records and their callers retain their protocol.
            triage = self._triage(answers)
        else:
            raise SpineAgentValidationError("不支持此问卷版本，请刷新并使用当前问卷")
        response_id = self._id()
        now = self._now()
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id, write=True)
            connection.execute(
                """
                INSERT INTO questionnaire_responses(id, case_id, protocol_version, answers_json, risk_level, triage_json, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (response_id, case_id, protocol_version, self._json(answers, "问卷答案"), triage["risk_level"], self._json(triage, "筛查结果"), user_id, now),
            )
            if triage["risk_level"] in {"urgent", "soon"}:
                self._create_review_task_unchecked(
                    connection,
                    case_id,
                    "triage",
                    "open",
                    "问卷分流提示需要线下复核",
                    {"questionnaire_id": response_id, "triage": triage},
                )
            connection.execute("UPDATE spine_cases SET updated_at = ? WHERE id = ?", (now, case_id))
        return {"id": response_id, "protocol_version": protocol_version, "answers": answers, "triage": triage, "created_at": now}

    def _questionnaire_row(self, row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "protocol_version": str(row["protocol_version"]),
            "answers": self._load_json(row["answers_json"], {}),
            "risk_level": str(row["risk_level"]),
            "triage": self._load_json(row["triage_json"], {}),
            "created_by": str(row["created_by"]),
            "created_at": int(row["created_at"]),
        }

    def latest_questionnaire(self, user_id: str, case_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id)
            row = connection.execute(
                "SELECT * FROM questionnaire_responses WHERE case_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (case_id,)
            ).fetchone()
        return self._questionnaire_row(row)

    def _create_review_task_unchecked(
        self, connection: sqlite3.Connection, case_id: str, category: str, state: str, summary: str, details: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = self._id()
        now = self._now()
        connection.execute(
            "INSERT INTO review_tasks(id, case_id, category, state, summary, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, case_id, category, state, self._clean_text(summary, "复核摘要", 240, required=True), self._json(details), now),
        )
        return {"id": task_id, "category": category, "state": state, "summary": summary, "details": details, "created_at": now}

    def create_review_task(self, user_id: str, case_id: str, category: str, summary: str, details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        category = self._clean_text(category, "复核分类", 48, required=True)
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id, write=True)
            return self._create_review_task_unchecked(connection, case_id, category, "open", summary, details or {})

    def resolve_review_task(self, user_id: str, case_id: str, task_id: str) -> bool:
        with self._connect() as connection:
            membership = self._require_access(connection, user_id, case_id, write=True)
            if str(membership["role"]) not in {"owner", "clinician"}:
                raise SpineAgentPermissionError("当前角色不能关闭复核任务")
            cursor = connection.execute(
                "UPDATE review_tasks SET state = 'resolved', resolved_at = ?, resolved_by = ? WHERE id = ? AND case_id = ? AND state = 'open'",
                (self._now(), user_id, task_id, case_id),
            )
            return cursor.rowcount > 0

    def _thread_for_case_unchecked(self, connection: sqlite3.Connection, case_id: str, user_id: str) -> str:
        row = connection.execute(
            "SELECT id FROM agent_threads WHERE case_id = ? AND created_by = ? ORDER BY created_at DESC LIMIT 1", (case_id, user_id)
        ).fetchone()
        if row:
            return str(row["id"])
        thread_id = self._id()
        connection.execute(
            "INSERT INTO agent_threads(id, case_id, created_by, created_at) VALUES (?, ?, ?, ?)", (thread_id, case_id, user_id, self._now())
        )
        return thread_id

    def append_agent_message(
        self, user_id: str, case_id: str, role: str, content: str, evidence: Optional[list[dict[str, Any]]] = None, audit: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system"}:
            raise SpineAgentValidationError("消息角色无效")
        clean_content = self._clean_text(content, "消息", MAX_MESSAGE_LENGTH, required=True)
        with self._connect() as connection:
            # A chat turn appends both the user request and the generated audit
            # record.  Read-only research collaborators therefore cannot use a
            # case chat as an indirect write channel.
            self._require_access(connection, user_id, case_id, write=True)
            thread_id = self._thread_for_case_unchecked(connection, case_id, user_id)
            message_id = self._id()
            now = self._now()
            connection.execute(
                "INSERT INTO agent_messages(id, thread_id, role, content, evidence_json, audit_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, thread_id, role, clean_content, self._json(evidence or []), self._json(audit or {}), now),
            )
        return {"id": message_id, "thread_id": thread_id, "role": role, "content": clean_content, "evidence": evidence or [], "audit": audit or {}, "created_at": now}

    def _action_row(self, row: sqlite3.Row, include_secret: bool = False) -> dict[str, Any]:
        item = {
            "id": str(row["id"]),
            "case_id": str(row["case_id"]),
            "thread_id": str(row["thread_id"] or ""),
            "kind": str(row["kind"]),
            "state": str(row["state"]),
            "reason": str(row["reason"]),
            "payload": self._load_json(row["payload_json"], {}),
            "result": self._load_json(row["result_json"], {}),
            "confirmation_expires_at": int(row["confirmation_expires_at"]),
            "created_by": str(row["created_by"]),
            "confirmed_by": str(row["confirmed_by"] or ""),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }
        if include_secret:
            item["confirmation_hash"] = str(row["confirmation_hash"])
        return item

    def propose_action(
        self, user_id: str, case_id: str, kind: str, reason: str, payload: Optional[dict[str, Any]] = None, thread_id: Optional[str] = None
    ) -> dict[str, Any]:
        kind = self._clean_text(kind, "动作类型", 64, required=True)
        if kind not in {"run_video_screening", "build_surface_archive", "run_ctmr_segmentation", "run_multicam_kinematics", "save_rehab_plan", "request_clinical_review", "export_case"}:
            raise SpineAgentValidationError("不允许的 Agent 动作")
        clean_reason = self._clean_text(reason, "动作说明", 300, required=True)
        token = secrets.token_urlsafe(24)
        now = self._now()
        expires = now + _ACTION_TOKEN_TTL_SECONDS
        action_id = self._id()
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id, write=True)
            if thread_id is None:
                thread_id = self._thread_for_case_unchecked(connection, case_id, user_id)
            connection.execute(
                """
                INSERT INTO agent_actions(id, case_id, thread_id, kind, state, reason, payload_json, result_json, confirmation_hash,
                    confirmation_expires_at, idempotency_key, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'draft', ?, ?, '{}', ?, ?, ?, ?, ?, ?)
                """,
                (action_id, case_id, thread_id, kind, clean_reason, self._json(payload or {}), hashlib.sha256(token.encode("utf-8")).hexdigest(), expires, self._id(), user_id, now, now),
            )
            row = connection.execute("SELECT * FROM agent_actions WHERE id = ?", (action_id,)).fetchone()
        item = self._action_row(row)
        item["confirmation_token"] = token
        item["requires_confirmation"] = True
        return item

    def _assessment_from_action(self, connection: sqlite3.Connection, action: dict[str, Any], user_id: str) -> dict[str, Any]:
        tool_id = action["kind"]
        spec = TOOL_REGISTRY[tool_id]
        kind = tool_id.removeprefix("run_")
        job_id = self._id()
        now = self._now()
        state = "queued" if spec.enabled else "needs_review"
        message = "已进入受控任务队列。" if spec.enabled else "该适配器尚未通过复现验收，任务已转为人工复核，不会伪造分析结果。"
        result = {"status_message": message, "reproduction_status": spec.reproduction_status, "non_diagnostic": True}
        connection.execute(
            """
            INSERT INTO assessment_jobs(id, case_id, kind, state, tool_id, tool_version, input_json, result_json, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, action["case_id"], kind, state, tool_id, spec.version, self._json(action["payload"]), self._json(result), user_id, now, now),
        )
        if state == "needs_review":
            self._create_review_task_unchecked(
                connection,
                action["case_id"],
                "third_party_adapter",
                "open",
                f"{tool_id} 已确认，等待复现验收或人工处理",
                {"assessment_job_id": job_id, "tool": spec.as_dict()},
            )
        return {"id": job_id, "kind": kind, "state": state, "tool_id": tool_id, "tool_version": spec.version, "result": result}

    def confirm_action(self, user_id: str, case_id: str, action_id: str, confirmation_token: str) -> dict[str, Any]:
        now = self._now()
        supplied_hash = hashlib.sha256(str(confirmation_token or "").encode("utf-8")).hexdigest()
        with self._connect() as connection:
            # Claim the draft and create any local result in one write transaction.
            # A Python lock in one HTTP process cannot protect another connection.
            connection.execute("BEGIN IMMEDIATE")
            self._require_access(connection, user_id, case_id, write=True)
            row = connection.execute("SELECT * FROM agent_actions WHERE id = ? AND case_id = ?", (action_id, case_id)).fetchone()
            if row is None:
                raise SpineAgentValidationError("待确认动作不存在")
            action = self._action_row(row, include_secret=True)
            if not hmac.compare_digest(action["confirmation_hash"], supplied_hash):
                raise SpineAgentPermissionError("确认令牌无效")
            if action["state"] != "draft":
                return self._action_row(row)
            if action["confirmation_expires_at"] < now:
                connection.execute("UPDATE agent_actions SET state = 'cancelled', updated_at = ? WHERE id = ?", (now, action_id))
                raise SpineAgentValidationError("确认已过期，请重新发起动作")
            state = "confirmed"
            result: dict[str, Any] = {"confirmed_at": now}
            connection.execute(
                "UPDATE agent_actions SET state = ?, confirmed_by = ?, updated_at = ?, result_json = ? WHERE id = ?",
                (state, user_id, now, self._json(result), action_id),
            )
            if action["kind"] == "run_video_screening" and action["payload"].get("source_job_id"):
                # The integrated gait runtime owns the real job and its review.
                # Do not create an obsolete adapter placeholder alongside it.
                result["dispatch_required"] = True
            elif action["kind"] in TOOL_REGISTRY:
                assessment = self._assessment_from_action(connection, action, user_id)
                state = assessment["state"]
                result = {"assessment_job": assessment}
            elif action["kind"] == "request_clinical_review":
                task = self._create_review_task_unchecked(
                    connection, case_id, "clinical_review", "open", "用户请求专业复核", action["payload"]
                )
                state = "completed"
                result = {"review_task": task}
            elif action["kind"] == "save_rehab_plan":
                try:
                    plan = self._create_rehab_plan_unchecked(connection, case_id, user_id)
                    state = "completed"
                    result = {"rehab_plan": plan}
                except SpineAgentValidationError as error:
                    state = "needs_review"
                    result = {"status_message": str(error), "non_diagnostic": True}
                    self._create_review_task_unchecked(
                        connection, case_id, "rehab_catalog", "open", "康复计划需要审核后的动作目录", action["payload"]
                    )
            elif action["kind"] == "export_case":
                state = "needs_review"
                result = {"status_message": "导出动作需要确认病例权限和脱敏范围后启用。"}
            connection.execute(
                "UPDATE agent_actions SET state = ?, result_json = ?, updated_at = ? WHERE id = ?",
                (state, self._json(result), self._now(), action_id),
            )
            row = connection.execute("SELECT * FROM agent_actions WHERE id = ?", (action_id,)).fetchone()
        return self._action_row(row)

    def list_actions(self, user_id: str, case_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id)
            rows = connection.execute(
                "SELECT * FROM agent_actions WHERE case_id = ? ORDER BY created_at DESC LIMIT ?", (case_id, max(1, min(int(limit), 100)))
            ).fetchall()
        return [self._action_row(row) for row in rows]

    def list_assessments(self, user_id: str, case_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id)
            rows = connection.execute(
                "SELECT * FROM assessment_jobs WHERE case_id = ? ORDER BY created_at DESC LIMIT ?", (case_id, max(1, min(int(limit), 100)))
            ).fetchall()
        return [
            {
                "id": str(row["id"]), "kind": str(row["kind"]), "state": str(row["state"]), "tool_id": str(row["tool_id"]),
                "tool_version": str(row["tool_version"]), "input": self._load_json(row["input_json"], {}),
                "result": self._load_json(row["result_json"], {}), "error_message": str(row["error_message"]),
                "created_at": int(row["created_at"]), "updated_at": int(row["updated_at"]),
            }
            for row in rows
        ]

    def _document_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]), "title": str(row["title"]), "source_url": str(row["source_url"]),
            "audience": str(row["audience"]), "topic": str(row["topic"]), "review_status": str(row["review_status"]),
            "usage_policy": str(row["usage_policy"]), "published_at": str(row["published_at"]), "expires_at": str(row["expires_at"]),
        }

    def search_knowledge(self, query: str, *, include_reference: bool = False, limit: int = 6) -> list[dict[str, Any]]:
        query = self._clean_text(query, "检索问题", MAX_MESSAGE_LENGTH, required=True)
        terms = [term for term in re.split(r"[^\w\u4e00-\u9fff]+", query.lower()) if len(term) >= 2]
        if not terms:
            terms = [query.lower()]
        allowed = ("operational", "approved") if not include_reference else ("operational", "approved", "reference_pending_clinical_review")
        placeholders = ",".join("?" for _ in allowed)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT c.*, d.title, d.source_url, d.audience, d.topic, d.review_status, d.usage_policy
                FROM knowledge_chunks c JOIN knowledge_documents d ON c.document_id = d.id
                WHERE d.review_status IN ({placeholders}) AND (d.expires_at = '' OR d.expires_at >= date('now'))
                """,
                allowed,
            ).fetchall()
        ranked: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            haystack = f"{row['title']} {row['heading']} {row['content']} {row['topic']}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                ranked.append((score, row))
        ranked.sort(key=lambda pair: (-pair[0], str(pair[1]["id"])))
        evidence = []
        document_counts: dict[str, int] = {}
        for score, row in ranked:
            document_id = str(row["document_id"])
            if document_counts.get(document_id, 0) >= 2:
                continue
            document_counts[document_id] = document_counts.get(document_id, 0) + 1
            evidence.append(
                {
                    "id": str(row["id"]), "document_id": document_id, "title": str(row["title"]),
                    "source_url": str(row["source_url"]), "heading": str(row["heading"]), "excerpt": str(row["content"]),
                    "review_status": str(row["review_status"]), "usage_policy": str(row["usage_policy"]), "score": score,
                }
            )
            if len(evidence) >= max(1, min(int(limit), 6)):
                break
        return evidence

    def knowledge_status(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT review_status, COUNT(*) AS count FROM knowledge_documents GROUP BY review_status").fetchall()
        counts = {str(row["review_status"]): int(row["count"]) for row in rows}
        return {
            "review_counts": counts,
            "retrieval": {"lexical": "SQLite term/FTS baseline", "semantic": "pending isolated BGE-M3 + Qdrant worker"},
            "rule": "reference_pending_clinical_review 内容不会用于自动个体化康复建议。",
        }

    def workspace(self, user_id: str, case_id: str) -> dict[str, Any]:
        case = self.get_case(user_id, case_id)
        with self._connect() as connection:
            self._require_access(connection, user_id, case_id)
            reviews = connection.execute(
                "SELECT * FROM review_tasks WHERE case_id = ? AND state = 'open' ORDER BY created_at DESC LIMIT 20", (case_id,)
            ).fetchall()
            messages = connection.execute(
                """
                SELECT m.* FROM agent_messages m JOIN agent_threads t ON m.thread_id = t.id
                WHERE t.case_id = ? ORDER BY m.created_at DESC LIMIT 12
                """,
                (case_id,),
            ).fetchall()
        return {
            "case": case,
            "consent": self.get_consent(user_id, case_id),
            "questionnaire": self.latest_questionnaire(user_id, case_id),
            "assessments": self.list_assessments(user_id, case_id),
            "actions": self.list_actions(user_id, case_id),
            "review_tasks": [
                {"id": str(row["id"]), "category": str(row["category"]), "state": str(row["state"]), "summary": str(row["summary"]),
                 "details": self._load_json(row["details_json"], {}), "created_at": int(row["created_at"])}
                for row in reviews
            ],
            "rehab_plans": self.list_rehab_plans(user_id, case_id),
            "messages": [
                {"id": str(row["id"]), "role": str(row["role"]), "content": str(row["content"]),
                 "evidence": self._load_json(row["evidence_json"], []), "audit": self._load_json(row["audit_json"], {}), "created_at": int(row["created_at"])}
                for row in reversed(messages)
            ],
            "tool_registry": [spec.as_dict() for spec in TOOL_REGISTRY.values()],
            "third_party_reproduction": list(THIRD_PARTY_REPRODUCTION_PLAN),
            "knowledge": self.knowledge_status(),
            "exercise_catalog": self.exercise_catalog_status(),
            "data_boundary": "原始医学影像、视频、照片、DICOM 与身份信息不会发送给外部大模型。",
        }

    def deidentified_case_summary(self, user_id: str, case_id: str) -> dict[str, Any]:
        """Only this whitelisted object may be passed to an external language model."""

        questionnaire = self.latest_questionnaire(user_id, case_id)
        assessments = self.list_assessments(user_id, case_id, limit=3)
        triage = questionnaire.get("triage", {}) if questionnaire else {}
        return {
            "case_scope": "adult_spine_followup",
            "triage": {"risk_level": triage.get("risk_level"), "automation_allowed": triage.get("automation_allowed")},
            "symptom_scores": {"pain_score": triage.get("pain_score"), "activity_limit": triage.get("activity_limit")},
            "assessment_states": [{"kind": item["kind"], "state": item["state"], "tool_version": item["tool_version"]} for item in assessments],
        }


class SpineHealthAgent:
    """Controlled ReAct-style coordinator with a maximum of one proposed tool per turn."""

    def __init__(
        self,
        store: SpineAgentStore,
        report_service: Any = None,
        case_facts_provider: Optional[Callable[[str, str], dict[str, Any]]] = None,
    ):
        self.store = store
        self.report_service = report_service
        self.case_facts_provider = case_facts_provider

    def _case_facts(self, user_id: str, case_id: str) -> dict[str, Any]:
        """Read an optional, already de-identified compatibility digest.

        The provider is deliberately narrow: it may supply structured facts
        from the pre-Agent case store, but not filenames, free text, raw assets,
        identifiers, or storage paths. A provider failure must never block the
        safety gate or cause the agent to invent a missing fact.
        """

        if self.case_facts_provider is None:
            return {"fact_count": 0, "modalities": [], "facts": []}
        try:
            facts = self.case_facts_provider(user_id, case_id)
        except Exception:
            return {"fact_count": 0, "modalities": [], "facts": []}
        if not isinstance(facts, dict):
            return {"fact_count": 0, "modalities": [], "facts": []}
        return {
            "fact_count": max(0, min(int(facts.get("fact_count", 0) or 0), 24)),
            "modalities": [str(item)[:48] for item in facts.get("modalities", []) if isinstance(item, str)][:8],
            "facts": [item for item in facts.get("facts", []) if isinstance(item, dict)][:12],
        }

    @staticmethod
    def _intent(message: str) -> str:
        lower = message.lower()
        if any(word in lower for word in ('不想分析', '不要分析', '不用分析', '暂不分析', '只想了解', '什么是', '是什么')):
            return 'evidence'
        if any(word in lower for word in ('康复', '锻炼', '训练计划', '训练动作')):
            return 'rehab'
        if any(word in lower for word in ("视频", "步态", "动作", "姿态")):
            return "video"
        if any(word in lower for word in ("照片", "三维", "体表", "点云")):
            return "surface"
        if any(word in lower for word in ("ct", "mri", "核磁", "磁共振")):
            return "ctmr"
        if any(word in lower for word in ("康复", "训练", "锻炼", "计划")):
            return "rehab"
        if any(word in lower for word in ("医生", "复核", "人工")):
            return "review"
        return "evidence"

    def respond(self, user_id: str, case_id: str, message: str) -> dict[str, Any]:
        user_message = self.store.append_agent_message(user_id, case_id, "user", message)
        questionnaire = self.store.latest_questionnaire(user_id, case_id)
        triage = questionnaire.get("triage", {}) if questionnaire else {}
        risk = str(triage.get("risk_level") or "unknown")
        case_facts = self._case_facts(user_id, case_id)
        evidence = self.store.search_knowledge(message, include_reference=False)
        intent = self._intent(message)
        actions: list[dict[str, Any]] = []
        tool_trace = ["case_access", "structured_case_facts", "triage", "retrieve_clinical_evidence"]

        if risk in {"urgent", "soon"}:
            text = f"{triage.get('message', '当前筛查需要线下复核。')} 为避免延误，系统不会在此生成自动训练或影像结论。"
            actions.append(self.store.propose_action(user_id, case_id, "request_clinical_review", "将当前筛查与已有资料整理为人工复核任务"))
        elif questionnaire is None or risk == "incomplete":
            text = "在解读或生成康复计划前，请先完成成人脊柱健康问卷。问卷采用最高风险规则，不会把严重信号与其他正常项平均抵消。"
        elif intent == "video":
            text = "视频动作/步态评估需要先上传并完成采集质量检查。当前适配器仍处于受控复现阶段，确认后只会创建可追踪的复核任务，不会伪造分析结果。"
            actions.append(self.store.propose_action(user_id, case_id, "run_video_screening", "申请运行已上传视频的受控姿态评估", {"protocol_version": "adult-motion-v1"}))
            tool_trace.append("run_video_screening:proposed")
        elif intent == "surface":
            text = "可以建立近似体表视觉档案，用于同一采集协议下的时间点对照。它不是诊断模型、不能替代 X 光测量，也不会与医学影像做几何叠加。"
            actions.append(self.store.propose_action(user_id, case_id, "build_surface_archive", "申请建立近似体表三维视觉档案", {"capture_protocol_version": "surface-archive-v1"}))
            tool_trace.append("build_surface_archive:proposed")
        elif intent == "ctmr":
            text = "CT/MR 分割属于专业影像任务，需要脱敏 DICOM/NIfTI、模型卡审核和人工复核。当前工具接口已建立，但不会在未验收状态下运行或输出结果。"
            actions.append(self.store.propose_action(user_id, case_id, "run_ctmr_segmentation", "申请创建 CT/MR 专业影像复核任务", {"modality": "unknown"}))
            tool_trace.append("run_ctmr_segmentation:proposed")
        elif intent == "rehab":
            catalog = self.store.exercise_catalog_status()
            if catalog["can_generate_plan"]:
                text = "当前可用目录包含经临床审核的成人动作定义。确认后，系统只会按该目录中的适用条件、禁忌、剂量边界和停止条件生成计划，不会让模型临场编造动作。"
            else:
                text = "个性化康复计划只能从完成临床审核的成人动作目录中生成。当前目录尚未签署，因此系统会保留你的目标并转人工审核，而不会编造动作、剂量或禁忌。"
            actions.append(self.store.propose_action(user_id, case_id, "save_rehab_plan", "申请在审核后的动作目录可用时生成个性化计划", {"source": "agent_request"}))
            tool_trace.append("save_rehab_plan:proposed")
        elif intent == "review":
            text = "我会把当前病例状态整理为一项可追踪的人工复核任务；病例所有者和受邀临床协作者可以在工作台处理它。"
            actions.append(self.store.propose_action(user_id, case_id, "request_clinical_review", "用户请求人工复核", {"source": "agent_chat"}))
        else:
            if evidence:
                text = "我已检索本地已启用的安全资料。当前可用内容主要用于解释系统边界；个体化医学建议需等待临床审核知识库和完整问卷。"
            else:
                text = "当前本地知识库没有足够的已审核证据来支持个体化结论。你可以先完成问卷、补充检测资料，或请求人工复核。"
            if case_facts["fact_count"]:
                modalities = "、".join(case_facts["modalities"]) or "既有病例资料"
                text += f" 当前病例还包含 {case_facts['fact_count']} 条已结构化归档的资料（{modalities}）；我会保留其来源与限制，不把它们合成为单一健康分数。"

        consent = self.store.get_consent(user_id, case_id)
        model_used = False
        if consent["external_ai_allowed"] and risk not in {"urgent", "soon"} and self.report_service is not None:
            summary = self.store.deidentified_case_summary(user_id, case_id)
            summary["structured_case_facts"] = case_facts
            rewritten = self.report_service.generate_agent_copy(summary, text)
            if rewritten.get("used") and rewritten.get("message"):
                text = str(rewritten["message"])
                model_used = True
                tool_trace.append("deepseek:deidentified_copy")

        audit = {
            "flow": "observe → policy → allowlisted_tool → validate → respond",
            "tool_trace": tool_trace,
            "risk_level": risk,
            "external_model_used": model_used,
            "no_raw_cot_stored": True,
            "evidence_count": len(evidence),
            "structured_case_fact_count": case_facts["fact_count"],
        }
        assistant_message = self.store.append_agent_message(user_id, case_id, "assistant", text, evidence=evidence, audit=audit)
        return {
            "message": assistant_message,
            "evidence": evidence,
            "result_cards": [
                {"type": "triage", "risk_level": risk, "message": triage.get("message", "请先完成问卷以确定安全边界。")},
                {"type": "case_facts", "fact_count": case_facts["fact_count"], "modalities": case_facts["modalities"], "message": "仅使用结构化病例事实；保留原有来源、质量和可比性限制。"},
                {"type": "data_boundary", "message": "原始影像、视频、照片与 DICOM 不会发送给外部大模型。"},
            ],
            "proposed_actions": actions,
            "safety_status": risk,
        }
