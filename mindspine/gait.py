"""Persistent, bounded, owner-scoped adapter for the local ScoNet V2.0.1 engine.

The web environment intentionally does not import torch, OpenCV, or ScoNet.
Each inference uses a fresh interpreter and a bounded process tree. SQLite is
the job source of truth; videos and artifacts live outside session TTL folders.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import threading
import time
from typing import BinaryIO, Callable
import uuid


ENGINE_VERSION = "2.0.1"
ACTIVE = ("uploading", "queued", "running")
TERMINAL = ("completed", "rejected", "failed", "cancelled")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
ARTIFACTS = {"report.json", "report_v2.json", "report.html", "report_v2.zip",
             "input_preview.mp4", "overlay.mp4", "masks.mp4", "contact_sheet.jpg",
             "silhouettes.npy", "tracks.json", "frame_map.json"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
DEFAULT_SOURCE = Path.home() / "Documents" / "ChatGPT" / "脊柱步态识别" / "ScoNetVideoApp"
ENGINE_MANIFEST = Path(__file__).with_name("gait_engine_manifest.json")


class GaitError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def validate_identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise GaitError(f"{name}不能为空或包含路径字符")
    return value


def validate_options(options: dict | None) -> dict:
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise GaitError("分析选项必须是 JSON 对象")
    allowed = {"start_seconds", "end_seconds", "target_track_id", "research_demo", "device",
               "analysis_mode", "allow_cpu_fallback"}
    if set(options) - allowed:
        raise GaitError("存在不支持的分析选项")
    result = {"start_seconds": None, "end_seconds": None, "target_track_id": None,
              "research_demo": False, "device": "auto", "analysis_mode": "single",
              "allow_cpu_fallback": False}
    result.update(options)
    for key in ("start_seconds", "end_seconds"):
        value = result[key]
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 3600 or not math.isfinite(value):
                raise GaitError("片段时间必须为 0–3600 秒内的有限数字")
    if result["end_seconds"] is not None:
        duration = result["end_seconds"] - (result["start_seconds"] or 0)
        if not 0 < duration <= 45:
            raise GaitError("所选分析片段须大于 0 秒且不超过 45 秒")
    target = result["target_track_id"]
    if target is not None and (type(target) is not int or not 0 <= target <= 1_000_000):
        raise GaitError("人物轨迹编号无效")
    if type(result["research_demo"]) is not bool or result["device"] not in ("auto", "cpu", "cuda"):
        raise GaitError("研究演示或推理设备选项无效")
    if result["analysis_mode"] not in ("single", "all") or type(result["allow_cpu_fallback"]) is not bool:
        raise GaitError("逐人分析或 CPU 回退选项无效")
    if result["analysis_mode"] == "all" and target is not None:
        raise GaitError("全部人物分析不能同时指定单个人物，请切换为单人分析")
    return result


def atomic_json(path: Path, document: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def verify_engine_source(source: Path) -> bool:
    """Pin the reviewed external engine code without copying its large assets."""
    try:
        manifest = json.loads(ENGINE_MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("engine_version") != ENGINE_VERSION or not manifest.get("files"):
            return False
        for relative, expected in manifest["files"].items():
            path = source / relative
            if source not in path.resolve().parents or path.is_symlink():
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


class GaitJobStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS gait_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, case_id TEXT NOT NULL,
                filename TEXT NOT NULL, extension TEXT NOT NULL, options_json TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                message TEXT NOT NULL, error_type TEXT, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, parent_job_id TEXT,
                review_notes TEXT NOT NULL DEFAULT '', reviewed_at REAL,
                archived INTEGER NOT NULL DEFAULT 0, archived_at REAL,
                selected INTEGER NOT NULL DEFAULT 0)""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(gait_jobs)")}
            if "request_key" not in columns:
                db.execute("ALTER TABLE gait_jobs ADD COLUMN request_key TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS gait_owner_case ON gait_jobs(user_id, case_id, created_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS gait_queue ON gait_jobs(status, created_at)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS gait_request_key ON gait_jobs(user_id,request_key) WHERE request_key IS NOT NULL")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.database_path), timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def decode(row) -> dict | None:
        if row is None:
            return None
        job = dict(row)
        job["options"] = json.loads(job.pop("options_json"))
        return job

    def reserve(self, user_id: str, case_id: str, filename: str, extension: str,
                options: dict, maximum: int, per_user: int, parent_job_id: str | None,
                request_key: str | None = None) -> dict:
        now, job_id = time.time(), uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if request_key:
                existing = self.decode(db.execute("SELECT * FROM gait_jobs WHERE user_id=? AND request_key=?", (user_id, request_key)).fetchone())
                if existing:
                    if existing["case_id"] != case_id or existing["parent_job_id"] != parent_job_id:
                        raise GaitError("确认动作已关联其他任务", 409)
                    existing["_newly_reserved"] = False
                    return existing
            count = db.execute("SELECT COUNT(*) FROM gait_jobs WHERE status IN ('uploading','queued','running')").fetchone()[0]
            user_count = db.execute("SELECT COUNT(*) FROM gait_jobs WHERE user_id=? AND status IN ('uploading','queued','running')", (user_id,)).fetchone()[0]
            if count >= maximum or user_count >= per_user:
                raise GaitError("分析队列已满，请等待已有任务结束后再提交", 429)
            db.execute("""INSERT INTO gait_jobs(id,user_id,case_id,filename,extension,options_json,
                status,stage,message,created_at,updated_at,parent_job_id,request_key)
                VALUES(?,?,?,?,?,?,'uploading','uploading','正在接收视频',?,?,?,?)""",
                (job_id, user_id, case_id, filename, extension, json.dumps(options), now, now, parent_job_id, request_key))
        job = self.get(job_id)
        job["_newly_reserved"] = True
        return job

    def get(self, job_id: str) -> dict | None:
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM gait_jobs WHERE id=?", (job_id,)).fetchone())

    def patch(self, job_id: str, *, only_statuses: tuple[str, ...] | None = None, **changes) -> bool:
        allowed = {"status", "stage", "progress", "message", "error_type"}
        if not changes or set(changes) - allowed:
            raise ValueError("Invalid job update")
        changes["updated_at"] = time.time()
        query = "UPDATE gait_jobs SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?"
        values = list(changes.values()) + [job_id]
        if only_statuses:
            query += " AND status IN (" + ",".join("?" for _ in only_statuses) + ")"
            values.extend(only_statuses)
        with self.connect() as db:
            return db.execute(query, values).rowcount > 0

    def claim(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Also prevents accidental duplicate services from running two GPUs.
            if db.execute("SELECT 1 FROM gait_jobs WHERE status='running' LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT * FROM gait_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE gait_jobs SET status='running',stage='initializing',message='正在启动独立推理进程',updated_at=? WHERE id=?", (time.time(), row["id"]))
            return self.decode(row)

    def recover_interrupted(self) -> None:
        with self.connect() as db:
            db.execute("""UPDATE gait_jobs SET status='failed',stage='interrupted',
                message='程序上次退出时任务中断，可复用原视频重新分析',error_type='Interrupted',updated_at=?
                WHERE status IN ('uploading','queued','running')""", (time.time(),))

    def list(self, user_id: str, case_id: str | None = None, limit: int = 50) -> list[dict]:
        condition, args = "user_id=?", [user_id]
        if case_id is not None:
            condition += " AND case_id=?"
            args.append(case_id)
        args.append(max(1, min(int(limit), 200)))
        with self.connect() as db:
            return [self.decode(row) for row in db.execute(f"SELECT * FROM gait_jobs WHERE {condition} ORDER BY created_at DESC LIMIT ?", args)]

    def counts(self) -> dict:
        with self.connect() as db:
            return {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) AS n FROM gait_jobs GROUP BY status")}

    def review(self, job: dict, notes: str, archive: bool | None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if archive is None:
                db.execute("UPDATE gait_jobs SET review_notes=?,reviewed_at=?,updated_at=? WHERE id=? AND user_id=?",
                           (notes, time.time(), time.time(), job["id"], job["user_id"]))
                return
            if archive:
                db.execute("UPDATE gait_jobs SET selected=0 WHERE user_id=? AND case_id=?", (job["user_id"], job["case_id"]))
            db.execute("""UPDATE gait_jobs SET review_notes=?,reviewed_at=?,archived=?,archived_at=?,selected=?,updated_at=?
                          WHERE id=? AND user_id=?""",
                       (notes, time.time(), int(archive), time.time() if archive else None,
                        int(archive), time.time(), job["id"], job["user_id"]))


class _ProcessTree:
    """Own exactly one inference process tree; never enumerate unrelated PIDs."""
    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        self.kernel = kernel
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = EXTENDED()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
            error = ctypes.get_last_error()
            kernel.CloseHandle(handle)
            raise ctypes.WinError(error)
        self.handle = handle

    def terminate(self) -> None:
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)
        elif os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif self.process.poll() is None:
            self.process.kill()  # Handshake has not released, so no descendants exist.

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class GaitService:
    def __init__(self, data_root: str | Path, source_root: str | Path | None = None,
                 python_executable: str | Path | None = None, *, max_active_jobs: int = 10,
                 max_user_jobs: int = 2, timeout_seconds: float = 1800,
                 max_upload_bytes: int = 512 * 1024 * 1024,
                 worker_script: str | Path | None = None):
        self.root = Path(data_root).resolve() / "gait"
        self.root.mkdir(parents=True, exist_ok=True)
        self.source = Path(source_root or os.getenv("MINDSPINE_GAIT_SOURCE_ROOT") or DEFAULT_SOURCE).resolve()
        configured_python = python_executable or os.getenv("MINDSPINE_GAIT_PYTHON")
        candidates = [self.source / folder / "Scripts" / "python.exe" for folder in (".build_gpu_venv", ".venv")]
        self.python = Path(configured_python or next((path for path in candidates if path.is_file()), candidates[0])).resolve()
        self.worker_script = Path(worker_script or Path(__file__).with_name("gait_worker.py")).resolve()
        self.max_active_jobs = max(1, min(int(max_active_jobs), 10))
        self.max_user_jobs = max(1, min(int(max_user_jobs), self.max_active_jobs))
        self.timeout_seconds = float(timeout_seconds)
        self.max_upload_bytes = int(max_upload_bytes)
        # A file lock enforces one scheduler per storage root across processes.
        self._instance_handle = (self.root / "scheduler.lock").open("a+b")
        if os.fstat(self._instance_handle.fileno()).st_size == 0:
            self._instance_handle.write(b"0")
            self._instance_handle.flush()
        self._instance_handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._instance_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._instance_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._instance_handle.close()
            raise RuntimeError("A gait scheduler is already using this data directory") from None
        self.store = GaitJobStore(self.root / "jobs.sqlite3")
        self.store.recover_interrupted()
        self._stop, self._wake = threading.Event(), threading.Event()
        self._lock = threading.RLock()
        self._processes: dict[str, _ProcessTree] = {}
        self._source_verified = verify_engine_source(self.source)
        self._thread = threading.Thread(target=self._schedule, name="gait-scheduler", daemon=True)
        self._thread.start()
        atexit.register(self.close)

    def health(self) -> dict:
        counts = self.store.counts()
        required = [self.source / "sconet_video" / "pipeline.py", self.source / "sconet_video" / "reporting.py",
                    self.source / "models" / "frontend_manifest.json", self.source / "vendor" / "ByteTrack" / "yolox" / "models" / "yolox.py"]
        available = self._source_verified and self.python.is_file() and self.worker_script.is_file() and all(path.is_file() for path in required) and not self._stop.is_set()
        return {"available": available, "engine_version": ENGINE_VERSION, "execution": "local_isolated_process",
                "message": "本机步态模块已连接" if available else "本机步态运行环境未就绪，请检查安装配置",
                "source_verified": self._source_verified,
                "device_policy": "gpu_preferred_explicit_cpu_fallback", "max_clip_seconds": 45,
                "max_upload_mb": self.max_upload_bytes // (1024 * 1024),
                "queue": {"active": sum(counts.get(key, 0) for key in ACTIVE), "running": counts.get("running", 0),
                          "queued": counts.get("queued", 0), "limit": self.max_active_jobs, "per_user_limit": self.max_user_jobs}}

    def _paths(self, job: dict) -> tuple[Path, Path, Path]:
        root = self.root / job["user_id"] / job["case_id"] / job["id"]
        return root, root / ("input" + job["extension"]), root / "output"

    @staticmethod
    def _public(job: dict) -> dict:
        return {"id": job["id"], "job_id": job["id"], "case_id": job["case_id"], "filename": job["filename"],
                "status": job["status"], "stage": job["stage"], "progress": job["progress"], "message": job["message"],
                "error_type": job["error_type"], "created_at": job["created_at"], "updated_at": job["updated_at"],
                "parent_job_id": job["parent_job_id"], "options": job["options"], "engine_version": ENGINE_VERSION,
                "review": {"notes": job["review_notes"], "reviewed_at": job["reviewed_at"],
                           "archived": bool(job["archived"]), "archived_at": job["archived_at"], "selected": bool(job["selected"])}}

    def _owned(self, user_id: str, job_id: str) -> dict:
        job = self.store.get(job_id)
        if not job or job["user_id"] != user_id:
            raise GaitError("步态任务不存在", 404)
        return job

    def _parent_cache(self, job: dict) -> Path | None:
        """Only supply a server-owned parent cache, never a client-supplied path.

        The worker separately verifies the input/options/model fingerprint. This
        boundary prevents even a damaged parent link or directory junction from
        exposing another case's files to that validation step.
        """
        parent_id = job.get("parent_job_id")
        if not parent_id:
            return None
        parent = self.store.get(parent_id)
        if (not parent or parent["user_id"] != job["user_id"] or
                parent["case_id"] != job["case_id"] or parent["status"] not in TERMINAL):
            return None
        try:
            for key in ("user_id", "case_id", "id"):
                validate_identifier(parent[key], key)
            output = self._paths(parent)[2]
            expected = self.root / parent["user_id"] / parent["case_id"] / parent["id"] / "output"
            if not output.is_dir() or output.resolve() != expected.absolute():
                return None
            return output
        except (OSError, ValueError, TypeError):
            return None

    def _runtime(self, job: dict) -> dict:
        """Read bounded display metadata; never expose arbitrary worker JSON."""
        path = self._paths(job)[0] / "progress.json"
        try:
            if path.is_symlink() or path.resolve() != path.absolute() or path.stat().st_size > 32768:
                return {}
            event = json.loads(path.read_text(encoding="utf-8"))
            runtime = event.get("runtime", {})
            if not isinstance(runtime, dict):
                return {}
            result = {}
            for key in ("device_requested", "device_effective", "requested_device", "actual_device", "device",
                        "detector_device", "classifier_device", "segmenter_device"):
                if runtime.get(key) in ("auto", "cpu", "cuda"):
                    result[key] = runtime[key]
            for key in ("cpu_fallback", "cache_used", "cpu_opt_in", "tracking_cache_reused"):
                if type(runtime.get(key)) is bool:
                    result[key] = runtime[key]
            for key in ("elapsed_seconds", "processed_frames", "total_frames"):
                value = runtime.get(key)
                if type(value) in (int, float) and 0 <= value <= 1_000_000_000 and math.isfinite(value):
                    result[key] = value
            name = runtime.get("gpu_name")
            if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9 ()_.+-]{1,100}", name):
                result["gpu_name"] = name
            reason = runtime.get("fallback_reason")
            if reason is None and "fallback_reason" in runtime:
                result["fallback_reason"] = None
            elif isinstance(reason, str) and re.fullmatch(r"GPU 计算自检失败（[A-Za-z0-9_/]{1,160}）", reason):
                result["fallback_reason"] = reason
            timings = runtime.get("stage_timings_seconds")
            if isinstance(timings, dict):
                result["stage_timings_seconds"] = {
                    key: value for key, value in timings.items()
                    if key in {"device_check", "model_loading", "tracking", "preview", "segmentation", "classification"}
                    and type(value) in (int, float) and 0 <= value <= 1_000_000_000 and math.isfinite(value)}
            return result
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def create(self, user_id: str, case_id: str, stream: BinaryIO, filename: str,
               options: dict | None = None, *, parent_job_id: str | None = None,
               idempotency_key: str | None = None) -> dict:
        validate_identifier(user_id, "用户标识")
        validate_identifier(case_id, "病例标识")
        options = validate_options(options)
        if parent_job_id is not None:
            parent = self._owned(user_id, parent_job_id)
            if parent["case_id"] != case_id or parent["status"] not in TERMINAL:
                raise GaitError("只能复用当前病例中已结束的任务", 409)
        if idempotency_key is not None:
            validate_identifier(idempotency_key, "请求标识")
        filename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1][:240]
        extension = Path(filename).suffix.lower()
        if extension not in VIDEO_EXTENSIONS:
            raise GaitError("请上传 MP4、MOV、M4V、AVI、MKV 或 WebM 视频", 422)
        with self._lock:
            if self._stop.is_set():
                raise GaitError("程序正在退出，无法接收新任务", 503)
            if not self.health()["available"]:
                raise GaitError("本机步态运行环境未就绪", 503)
            job = self.store.reserve(user_id, case_id, filename, extension, options,
                                     self.max_active_jobs, self.max_user_jobs, parent_job_id, idempotency_key)
            if not job["_newly_reserved"]:
                return self._public(job)
        root, input_path, output = self._paths(job)
        try:
            output.mkdir(parents=True, exist_ok=True)
            written = 0
            with input_path.open("xb") as destination:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self.max_upload_bytes:
                        raise GaitError("视频文件超过本机上传大小限制", 413)
                    if self._stop.is_set() or self.store.get(job["id"])["status"] == "cancelled":
                        raise GaitError("视频接收已取消", 409)
                    destination.write(chunk)
            if not written:
                raise GaitError("视频文件为空", 422)
            self.store.patch(job["id"], only_statuses=("uploading",), status="queued", stage="queued", message="已加入本机分析队列")
            self._wake.set()
            return self.get(user_id, job["id"])
        except Exception as error:
            input_path.unlink(missing_ok=True)
            self.store.patch(job["id"], only_statuses=("uploading",), status="failed", stage="upload_failed",
                             message="视频接收失败，请重新提交", error_type=type(error).__name__)
            raise

    def get(self, user_id: str, job_id: str) -> dict:
        job = self._owned(user_id, job_id)
        result = self._public(job)
        result["runtime"] = self._runtime(job)
        return result

    def list(self, user_id: str, case_id: str | None = None, limit: int = 50) -> list[dict]:
        return [self._public(job) for job in self.store.list(user_id, case_id, limit)]

    def report(self, user_id: str, job_id: str) -> dict:
        job = self._owned(user_id, job_id)
        if job["status"] not in {"completed", "rejected"}:
            raise GaitError("报告尚未生成", 409)
        output = self._paths(job)[2]
        report_path = output / "report_v2.json"
        if not report_path.is_file() or report_path.is_symlink():
            raise GaitError("报告文件不存在", 404)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["job_id"] = job_id
        report["case_id"] = job["case_id"]
        report["review"] = self._public(job)["review"]
        names = {name for name in report.get("artifacts", {}).values() if isinstance(name, str) and name in ARTIFACTS}
        report["file_urls"] = {name: f"/api/gait/jobs/{job_id}/files/{name}" for name in names
                               if (output / name).is_file() and not (output / name).is_symlink()}
        return report

    def artifact(self, user_id: str, job_id: str, filename: str) -> Path:
        if filename not in ARTIFACTS:
            raise GaitError("结果文件不存在", 404)
        report = self.report(user_id, job_id)
        if filename not in report["file_urls"]:
            raise GaitError("结果文件不存在", 404)
        job = self._owned(user_id, job_id)
        output = self._paths(job)[2].resolve()
        path = output / filename
        if path.is_symlink() or path.resolve().parent != output:
            raise GaitError("结果文件不存在", 404)
        return path

    def cancel(self, user_id: str, job_id: str) -> dict:
        job = self._owned(user_id, job_id)
        with self._lock:
            self.store.patch(job_id, only_statuses=ACTIVE, status="cancelled", stage="cancelled", message="任务已取消")
            tree = self._processes.get(job_id)
            if tree:
                tree.terminate()
                try:
                    tree.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self._wake.set()
        return self.get(user_id, job["id"])

    def cancel_user(self, user_id: str) -> list[dict]:
        """Cancel this user's unfinished work on logout, preserving all history."""
        validate_identifier(user_id, "用户标识")
        with self._lock:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                ids = [row["id"] for row in db.execute(
                    "SELECT id FROM gait_jobs WHERE user_id=? AND status IN ('uploading','queued','running')", (user_id,))]
                db.execute("""UPDATE gait_jobs SET status='cancelled',stage='cancelled',
                              message='账号已退出，任务已取消',updated_at=?
                              WHERE user_id=? AND status IN ('uploading','queued','running')""", (time.time(), user_id))
            for job_id in ids:
                tree = self._processes.get(job_id)
                if tree:
                    tree.terminate()
                    try:
                        tree.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
        self._wake.set()
        return [self.get(user_id, job_id) for job_id in ids]

    def rerun(self, user_id: str, job_id: str, options: dict | None = None, *, idempotency_key: str | None = None) -> dict:
        job = self._owned(user_id, job_id)
        if options is not None and not isinstance(options, dict):
            raise GaitError("分析选项必须是 JSON 对象")
        if job["status"] not in TERMINAL:
            raise GaitError("请等待当前任务结束或先取消任务", 409)
        input_path = self._paths(job)[1]
        if not input_path.is_file() or input_path.is_symlink():
            raise GaitError("原视频不存在，请重新上传", 404)
        merged = dict(job["options"])
        merged.update(options or {})
        with input_path.open("rb") as stream:
            return self.create(user_id, job["case_id"], stream, job["filename"], merged,
                               parent_job_id=job_id, idempotency_key=idempotency_key)

    def review_archive(self, user_id: str, job_id: str, notes: str, archive: bool | None = None) -> dict:
        job = self._owned(user_id, job_id)
        if job["status"] not in {"completed", "rejected"}:
            raise GaitError("只有已生成报告的任务可以复核", 409)
        if not isinstance(notes, str) or len(notes) > 4000 or (archive is not None and type(archive) is not bool):
            raise GaitError("复核备注限 4000 字，归档选项必须是布尔值")
        notes = notes.strip()
        if archive and not notes:
            raise GaitError("请先填写复核备注再归档")
        if archive:
            report = self.report(user_id, job_id)
            if (job["options"].get("analysis_mode") == "all" or report.get("multi_subject") is True or
                    len(report.get("subject_results") or []) > 1):
                raise GaitError("多人结果不能直接归入单人病例，请先选择实际被测者并进行单人分析后归档", 409)
        self.store.review(job, notes, archive)
        return self.get(user_id, job_id)

    def selected_result(self, user_id: str, case_id: str) -> dict | None:
        with self.store.connect() as db:
            row = db.execute("SELECT id FROM gait_jobs WHERE user_id=? AND case_id=? AND selected=1 AND archived=1 ORDER BY archived_at DESC LIMIT 1", (user_id, case_id)).fetchone()
        return self.report(user_id, row["id"]) if row else None

    def _schedule(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.store.claim()
                if job:
                    self._execute(job)
                    continue
            except Exception:
                # A scheduler DB error must not kill the only dispatch thread.
                pass
            self._wake.wait(0.25)
            self._wake.clear()

    def _execute(self, job: dict) -> None:
        root, input_path, output = self._paths(job)
        tree = process = None
        try:
            spec = {"id": job["id"], "source_root": str(self.source),
                    "input_path": str(input_path), "output_dir": str(output), "filename": job["filename"],
                    "options": validate_options(job["options"])}
            cache_dir = self._parent_cache(job)
            if cache_dir is not None:
                spec["cache_dir"] = str(cache_dir)
            atomic_json(root / "worker_spec.json", spec)
            with self._lock:
                if self._stop.is_set() or self.store.get(job["id"])["status"] != "running":
                    return
                environment = os.environ.copy()
                environment["PYTHONDONTWRITEBYTECODE"] = "1"
                environment["PYTHONUNBUFFERED"] = "1"
                process = subprocess.Popen([str(self.python), str(self.worker_script), "--spec", str(root / "worker_spec.json")],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd=str(root), env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=os.name != "nt")
                tree = _ProcessTree(process)
                self._processes[job["id"]] = tree
                process.stdin.write(b"start\n")
                process.stdin.flush()
                process.stdin.close()
            started = time.monotonic()
            last_progress = None
            while process.poll() is None:
                if self._stop.is_set() or self.store.get(job["id"])["status"] == "cancelled":
                    tree.terminate()
                    break
                if time.monotonic() - started > self.timeout_seconds:
                    tree.terminate()
                    self.store.patch(job["id"], only_statuses=("running",), status="failed", stage="timeout",
                                     message="分析超过本机时间限制，可缩短片段后重试", error_type="Timeout")
                    break
                try:
                    progress_text = (root / "progress.json").read_text(encoding="utf-8")
                    if progress_text != last_progress:
                        event = json.loads(progress_text)
                        value = float(event.get("progress", 0)) * 100
                        if math.isfinite(value):
                            self.store.patch(job["id"], only_statuses=("running",), stage=str(event.get("stage", "running"))[:80],
                                             message=str(event.get("message", "正在分析视频"))[:500], progress=max(0, min(value, 99)))
                        last_progress = progress_text
                except (OSError, ValueError, TypeError):
                    pass
                self._stop.wait(0.2)
            process.wait(timeout=10)
            if self.store.get(job["id"])["status"] != "running":
                return
            result = json.loads((root / "worker_result.json").read_text(encoding="utf-8"))
            status = result.get("status")
            if status not in {"completed", "rejected", "failed"}:
                raise ValueError("Unexpected worker result")
            if status in {"completed", "rejected"}:
                document = json.loads((output / "report_v2.json").read_text(encoding="utf-8"))
                if process.returncode != 0 or document.get("report_view_version") != ENGINE_VERSION:
                    raise ValueError("Worker report version mismatch")
                if not all((output / name).is_file() for name in ("report_v2.json", "report.html", "report_v2.zip")):
                    raise ValueError("Incomplete report artifacts")
            self.store.patch(job["id"], only_statuses=("running",), status=status, stage="done" if status != "failed" else "error",
                             progress=100 if status != "failed" else 0, message=str(result.get("message", "分析结束"))[:500],
                             error_type=result.get("error_type"))
        except Exception as error:
            self.store.patch(job["id"], only_statuses=("running",), status="failed", stage="error",
                             message="本机步态任务执行失败，可检查环境后重试", error_type=type(error).__name__)
        finally:
            with self._lock:
                self._processes.pop(job["id"], None)
                if tree:
                    tree.terminate()
                    tree.close()
                elif process and process.poll() is None:
                    process.kill()
                if process:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass

    def close(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            self._stop.set()
            self._wake.set()
            with self.store.connect() as db:
                db.execute("UPDATE gait_jobs SET status='cancelled',stage='cancelled',message='程序退出，任务已取消',updated_at=? WHERE status IN ('uploading','queued','running')", (time.time(),))
            for tree in self._processes.values():
                tree.terminate()
        self._thread.join(timeout=20)
        self._instance_handle.close()
        atexit.unregister(self.close)


# Friendly alias for integrations referring to a generic JobStore.
JobStore = GaitJobStore


def create_router(service: GaitService, get_user: Callable, authorize_case: Callable | None = None):
    """Convenient import while keeping FastAPI out of the worker/store module."""
    from mindspine.gait_api import create_router as build_router
    return build_router(service, get_user, authorize_case)
