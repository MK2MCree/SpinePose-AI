"""Case-owned Agent attachments and persistent, bounded local inference runs.

The planner only dispatches explicitly selected plugins against compatible
attachments. Model code lives in injected adapters; no model-generated paths,
SQL, or tool names are executable here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

LOGGER = logging.getLogger(__name__)
PLUGINS = (
    {"id": "keypoints", "label": "关键点检测", "description": "侧位 DR / DICOM 椎体关键点标注与角度测算", "accepts": ["xray"]},
    {"id": "roi", "label": "ROI 区域识别", "description": "体表 RGB 脊柱区域检测与裁剪", "accepts": ["rgb"]},
    {"id": "gait", "label": "步态识别", "description": "视频质控、人体剪影与步态分析", "accepts": ["video"]},
    {"id": "pseudo_dr", "label": "脊柱伪 DR 生成", "description": "Pix2Pix 左半图直推 · 配对图自动取左半，不经过 ROI / RGB 迁移", "accepts": ["rgb"]},
)
PLUGIN_BY_ID = {item["id"]: item for item in PLUGINS}
EXTENSIONS = {
    "xray": {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".dcm", ".dicom"},
    "rgb": {".png", ".jpg", ".jpeg", ".bmp", ".webp"},
    "video": {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"},
    "document": {".pdf", ".txt", ".md", ".csv", ".docx"},
}
TERMINAL = {"completed", "needs_review", "failed", "cancelled"}
# Keep headroom below the case store's 96 KiB audit limit. Full model output
# belongs to the run record; chat only needs a bounded renderable snapshot.
CHAT_AUDIT_BYTES = 80 * 1024
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,96}$")


class AgentToolError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class AgentToolService:
    def __init__(self, root: Path, case_store, executor: Callable, availability: Callable,
                 *, max_upload_bytes: int = 64 * 1024 * 1024,
                 max_video_bytes: int = 512 * 1024 * 1024, record_result: Callable | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "agent_tools.sqlite3"
        self.case_store = case_store
        self.execute = executor
        self.availability = availability
        self.record_result = record_result
        self.max_upload_bytes = max_upload_bytes
        self.max_video_bytes = max_video_bytes
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._cancel: dict[str, threading.Event] = {}
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-tools")
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, case_id TEXT NOT NULL,
                    filename TEXT NOT NULL, modality TEXT NOT NULL, extension TEXT NOT NULL,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, case_id TEXT NOT NULL,
                    request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                    body TEXT NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(owner, case_id, request_key));
                CREATE INDEX IF NOT EXISTS agent_runs_case ON runs(owner,case_id,created_at);
            """)
            for row in db.execute("SELECT id,owner,body FROM runs WHERE state IN ('queued','running','cancelling')").fetchall():
                run = json.loads(row["body"])
                run.update(state="failed", message="程序已重启，此任务已中止，请重新提交。", updated_at=time.time())
                for step in run["steps"]:
                    if step["state"] not in TERMINAL:
                        step.update(state="failed", message="程序重启，任务中止")
                try:
                    self._append_result(row["owner"], run)
                except Exception:
                    LOGGER.warning("Interrupted Agent run message could not be saved")
                db.execute("UPDATE runs SET state='failed',body=? WHERE id=?", (self._json(run), row["id"]))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    def _access(self, owner: str, case_id: str, write: bool = False):
        try:
            if write:
                self.case_store.require_write(owner, case_id)
            else:
                self.case_store.get_case(owner, case_id)
        except PermissionError:
            raise AgentToolError("无权访问或修改此病例", 403) from None

    def plugins(self):
        health = self.availability()
        return [{**item, **health.get(item["id"], {"available": False, "reason": "模块未配置"})} for item in PLUGINS]

    def _attachment(self, owner, case_id, attachment_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM attachments WHERE id=? AND owner=? AND case_id=?",
                             (attachment_id, owner, case_id)).fetchone()
        if not row:
            raise AgentToolError("附件不存在或不属于当前病例", 404)
        return dict(row)

    def _attachment_path(self, attachment):
        extension = ".dcm" if attachment["extension"] == ".dicom" else attachment["extension"]
        return self.root / "attachments" / attachment["id"] / ("source" + extension)

    def _public_attachment(self, attachment):
        url = f"/api/cases/{attachment['case_id']}/agent/attachments/{attachment['id']}"
        kind = "dicom" if attachment["extension"] in {".dcm", ".dicom"} else (
            "image" if attachment["modality"] in {"rgb", "xray"} else attachment["modality"])
        return {key: attachment[key] for key in ("id", "case_id", "filename", "modality", "size", "sha256", "created_at")} | {
            "kind": kind, "url": url, "preview_url": url if kind in {"image", "video"} else None}

    def upload(self, owner: str, case_id: str, stream: BinaryIO, filename: str, modality: str):
        self._access(owner, case_id, True)
        filename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1][:240]
        extension = Path(filename).suffix.lower()
        if modality not in EXTENSIONS or extension not in EXTENSIONS[modality]:
            raise AgentToolError("文件类型与所选资料类型不匹配", 422)
        attachment = {"id": uuid.uuid4().hex, "owner": owner, "case_id": case_id, "filename": filename,
                      "modality": modality, "extension": extension, "created_at": time.time()}
        path = self._attachment_path(attachment)
        path.parent.mkdir(parents=True)
        size, digest = 0, hashlib.sha256()
        try:
            with path.open("xb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > (self.max_video_bytes if modality == "video" else self.max_upload_bytes):
                        raise AgentToolError("附件超过上传大小限制", 413)
                    digest.update(chunk)
                    output.write(chunk)
            if not size:
                raise AgentToolError("附件为空", 422)
            if modality in {"rgb", "xray"}:
                if extension in {".dcm", ".dicom"}:
                    import pydicom
                    dataset = pydicom.dcmread(path, stop_before_pixels=True)
                    if not getattr(dataset, "Rows", 0) or not getattr(dataset, "Columns", 0):
                        raise AgentToolError("DICOM 文件不包含可分析的影像", 422)
                    if int(getattr(dataset, "NumberOfFrames", 1)) != 1:
                        raise AgentToolError("当前 DR 插件仅支持单帧 DICOM，请提供一张侧位 DR 影像", 422)
                    if int(dataset.Rows) * int(dataset.Columns) > 60_000_000:
                        raise AgentToolError("DICOM 图像尺寸过大，请上传较小影像", 422)
                    if str(getattr(dataset, "Modality", "")).upper() in {"CT", "MR", "US", "PT", "NM"}:
                        raise AgentToolError("此 DICOM 模态不适用 DR 关键点模型，请提供 DR / X 光影像", 422)
                else:
                    from PIL import Image
                    with Image.open(path) as image:
                        if image.width * image.height > 60_000_000:
                            raise AgentToolError("图像尺寸过大，请上传较小影像", 422)
                        image.verify()
            attachment.update(size=size, sha256=digest.hexdigest())
            with self._db() as db:
                db.execute("INSERT INTO attachments VALUES (?,?,?,?,?,?,?,?,?)", tuple(attachment[key] for key in (
                    "id", "owner", "case_id", "filename", "modality", "extension", "size", "sha256", "created_at")))
        except AgentToolError:
            path.unlink(missing_ok=True)
            raise
        except Exception:
            path.unlink(missing_ok=True)
            raise AgentToolError("无法读取附件，请检查文件格式是否完整", 422) from None
        return self._public_attachment(attachment)

    def attachment_file(self, owner, case_id, attachment_id):
        self._access(owner, case_id)
        attachment = self._attachment(owner, case_id, attachment_id)
        path = self._attachment_path(attachment)
        if not path.is_file() or path.is_symlink():
            raise AgentToolError("附件文件不存在", 404)
        return path, self._public_attachment(attachment)

    def list(self, owner, case_id):
        self._access(owner, case_id)
        with self._db() as db:
            return [json.loads(row["body"]) for row in db.execute(
                "SELECT body FROM runs WHERE owner=? AND case_id=? ORDER BY created_at DESC LIMIT 30", (owner, case_id))]

    def get(self, owner, case_id, run_id):
        self._access(owner, case_id)
        with self._db() as db:
            row = db.execute("SELECT body FROM runs WHERE id=? AND owner=? AND case_id=?", (run_id, owner, case_id)).fetchone()
        if not row:
            raise AgentToolError("任务不存在或不属于当前病例", 404)
        return json.loads(row["body"])

    def _save(self, run):
        run["updated_at"] = time.time()
        with self._db() as db:
            db.execute("UPDATE runs SET body=?,state=? WHERE id=?", (self._json(run), run["state"], run["id"]))

    def start(self, owner, case_id, message, attachment_ids, plugin_ids, idempotency_key):
        self._access(owner, case_id, True)
        if self._stop.is_set():
            raise AgentToolError("程序正在退出", 503)
        message = str(message or "").strip()
        if not message or len(message) > 4000:
            raise AgentToolError("请输入 1–4000 字的问题", 422)
        if not IDENTIFIER.fullmatch(str(idempotency_key or "")):
            raise AgentToolError("请求标识无效", 422)
        if not 1 <= len(attachment_ids) <= 8 or len(set(attachment_ids)) != len(attachment_ids):
            raise AgentToolError("请选择 1–8 个不同附件", 422)
        if len(plugin_ids) > 4 or len(set(plugin_ids)) != len(plugin_ids) or set(plugin_ids) - PLUGIN_BY_ID.keys():
            raise AgentToolError("选择了不支持的插件", 422)
        attachments = [self._attachment(owner, case_id, identifier) for identifier in attachment_ids]
        fingerprint = hashlib.sha256(self._json([message, attachment_ids, plugin_ids]).encode()).hexdigest()
        with self._lock:
            with self._db() as db:
                row = db.execute("SELECT body,fingerprint FROM runs WHERE owner=? AND case_id=? AND request_key=?",
                                 (owner, case_id, idempotency_key)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise AgentToolError("请求标识已用于另一项任务，请重新发送", 409)
                return json.loads(row["body"])
            health = {item["id"]: item for item in self.plugins()}
            steps = []
            for plugin_id in plugin_ids:
                spec = health[plugin_id]
                matching = [item for item in attachments if item["modality"] in spec["accepts"]]
                if not matching:
                    raise AgentToolError(f"{spec['label']}没有匹配的资料，请核对图片的 DR / 体表类型", 422)
                if not spec["available"]:
                    raise AgentToolError(spec.get("reason") or f"{spec['label']}未就绪", 503)
                for item in matching:
                    steps.append({"id": uuid.uuid4().hex, "plugin_id": plugin_id, "label": spec["label"],
                                  "attachment_id": item["id"], "state": "queued", "message": "等待执行"})
            if len(steps) > 16:
                raise AgentToolError("单次最多执行 16 项分析，请分批发送", 422)
            now = time.time()
            run = {"id": uuid.uuid4().hex, "case_id": case_id, "state": "queued", "message": "已加入分析队列",
                   "request": message, "attachments": [self._public_attachment(item) for item in attachments],
                   "plugin_ids": plugin_ids, "steps": steps, "result_cards": [], "errors": [],
                   "unprocessed_attachment_ids": [item['id'] for item in attachments if item['id'] not in {step['attachment_id'] for step in steps}],
                   "created_at": now, "updated_at": now}
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                count = db.execute("SELECT COUNT(*) FROM runs WHERE state IN ('queued','running','cancelling')").fetchone()[0]
                user_count = db.execute("SELECT COUNT(*) FROM runs WHERE owner=? AND state IN ('queued','running','cancelling')", (owner,)).fetchone()[0]
                if count >= 8 or user_count >= 2:
                    raise AgentToolError("当前分析队列已满，请等待任务完成", 429)
                db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)", (run["id"], owner, case_id, idempotency_key,
                           fingerprint, "queued", self._json(run), now))
            try:
                self.case_store.append_agent_message(owner, case_id, "user", message, audit={
                    "tool_run_id": run["id"], "attachments": run["attachments"], "plugin_ids": plugin_ids})
            except Exception:
                run.update(state="failed", message="病例消息保存失败，尚未执行分析")
                self._save(run)
                raise
            event = threading.Event()
            self._cancel[run["id"]] = event
            self._pool.submit(self._work, owner, run, attachments, event)
            return json.loads(self._json(run))

    def _work(self, owner, run, attachments, event):
        by_id = {item["id"]: item for item in attachments}
        try:
            with self._lock:
                if event.is_set():
                    run.update(state="cancelled", message="分析已取消")
                    for step in run['steps']:
                        step.update(state="cancelled", message="已取消")
                    self._append_result(owner, run)
                    self._save(run)
                    return
                run.update(state="running", message="正在执行已选插件")
                self._save(run)
            for step in run["steps"]:
                if event.is_set() or self._stop.is_set():
                    break
                try:
                    self._access(owner, run["case_id"], True)
                    with self._lock:
                        if event.is_set():
                            break
                        step.update(state="running", message="正在分析")
                        self._save(run)
                    attachment = by_id[step["attachment_id"]]
                    output = self.root / "runs" / run["id"] / step["id"]
                    output.mkdir(parents=True, exist_ok=True)
                    card = self.execute(owner, run["case_id"], run["id"], step, attachment,
                                        self._attachment_path(attachment), output, event)
                    self._access(owner, run["case_id"], True)
                    with self._lock:
                        if event.is_set() or self._stop.is_set():
                            break
                        card.update(plugin_id=step["plugin_id"], attachment_id=attachment["id"], source="model")
                        if self.record_result:
                            card["archive_entry_id"] = self.record_result(owner, run["case_id"], run["id"], attachment, card)
                        step.update(state=card.get("status", "completed"), message=card.get("summary", "分析完成"))
                        run["result_cards"].append(card)
                        self._save(run)
                except Exception as error:
                    LOGGER.warning("Agent plugin %s failed (%s)", step["plugin_id"], type(error).__name__)
                    safe = str(error) if isinstance(error, AgentToolError) else "模块执行失败，请检查模型与资料后重试"
                    with self._lock:
                        if event.is_set() or self._stop.is_set():
                            break
                        step.update(state="failed", message=safe)
                        run["errors"].append({"plugin_id": step["plugin_id"], "message": safe})
                        self._save(run)
            with self._lock:
                if event.is_set() or self._stop.is_set():
                    run.update(state="cancelled", message="分析已取消")
                    for step in run["steps"]:
                        if step["state"] not in TERMINAL:
                            step.update(state="cancelled", message="已取消")
                elif run["errors"]:
                    run.update(state="needs_review" if run["result_cards"] else "failed", message="部分分析未完成" if run["result_cards"] else "分析未完成")
                elif any(step["state"] == "needs_review" for step in run["steps"]):
                    run.update(state="needs_review", message="分析结束，部分结果需要复核")
                else:
                    run.update(state="completed", message="分析完成" if run["steps"] else "附件已保存")
                self._append_result(owner, run)
                self._save(run)
        except Exception:
            LOGGER.warning("Agent run could not finish saving")
            with self._lock:
                run.update(state="failed", message="任务保存失败，请检查病例访问权限")
                self._save(run)
        finally:
            with self._lock:
                self._cancel.pop(run["id"], None)

    def _append_result(self, owner, run):
        if not run["steps"] and run["state"] == "completed":
            content = "附件已保存到当前病例。本次没有选择分析插件；文件内容尚未解析，也没有产生医学结论。图片或视频可选择匹配插件后继续分析。"
        else:
            lines = [run["message"] + "。"]
            lines.extend(f"{step['label']}：{step['message']}" for step in run["steps"])
            if run.get('unprocessed_attachment_ids'):
                lines.append(f"另有 {len(run['unprocessed_attachment_ids'])} 份附件没有匹配的插件，仅保存，尚未解析。")
            content = "\n\n".join(lines)[:3900]
        audit = {
            "tool_run_id": run["id"], "tool_steps": run["steps"], "result_cards": run["result_cards"],
            "safety_status": run["state"], "execution": "local_models"}
        if len(self._json(audit).encode("utf-8")) > CHAT_AUDIT_BYTES:
            limits = {"type": 32, "title": 160, "summary": 500, "status": 40,
                      "plugin_id": 40, "attachment_id": 64, "source": 16, "model_version": 80}
            audit["result_cards"] = [
                {key: str(card[key])[:limit] for key, limit in limits.items() if key in card}
                for card in run["result_cards"][:16]
            ]
            audit["tool_steps"] = [
                {key: str(step.get(key, ""))[:limit] for key, limit in
                 {"id": 64, "plugin_id": 40, "label": 80, "state": 40, "message": 300}.items()}
                for step in run["steps"][:16]
            ]
            audit["result_cards_truncated"] = True
            # Defensive ceiling even if an adapter supplies unusual Unicode.
            if len(self._json(audit).encode("utf-8")) > CHAT_AUDIT_BYTES:
                audit["result_cards"] = []
                audit["tool_steps"] = []
        self.case_store.append_agent_message(owner, run["case_id"], "assistant", content, audit=audit)

    def cancel(self, owner, case_id, run_id):
        self._access(owner, case_id, True)
        with self._lock:
            run = self.get(owner, case_id, run_id)
            if run["state"] in TERMINAL:
                return run
            event = self._cancel.get(run_id)
            if event:
                event.set()
            # Do not expose a terminal state until the worker has stored its
            # assistant response. Pollers otherwise stop before that message exists.
            run.update(state="cancelling", message="正在取消；当前模型计算结束后停止后续任务")
            for step in run["steps"]:
                if step["state"] not in TERMINAL:
                    step.update(state="cancelling", message="正在取消")
            self._save(run)
            return run

    @staticmethod
    def artifact_url(case_id, run_id, step_id, name):
        return f"/api/cases/{case_id}/agent/tool-runs/{run_id}/files/{step_id}/{name}"

    def artifact(self, owner, case_id, run_id, step_id, filename):
        run = self.get(owner, case_id, run_id)
        if step_id not in {step["id"] for step in run["steps"]} or Path(filename).name != filename:
            raise AgentToolError("结果文件不存在", 404)
        url = self.artifact_url(case_id, run_id, step_id, filename)
        allowed = {image.get("url") for card in run["result_cards"] for image in card.get("images", [])}
        if url not in allowed:
            raise AgentToolError("结果文件不存在", 404)
        path = self.root / "runs" / run_id / step_id / filename
        if not path.is_file() or path.is_symlink():
            raise AgentToolError("结果文件不存在", 404)
        return path

    def close(self):
        self._stop.set()
        with self._lock:
            for event in self._cancel.values():
                event.set()
        self._pool.shutdown(wait=False, cancel_futures=True)


class ToolRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    attachment_ids: list[str] = Field(min_length=1, max_length=8)
    plugin_ids: list[str] = Field(default_factory=list, max_length=4)
    idempotency_key: str = Field(min_length=1, max_length=96)


def create_router(service: AgentToolService, get_user: Callable) -> APIRouter:
    router = APIRouter(tags=["agent-tools"])

    def owner(request):
        user = get_user(request)
        return user if isinstance(user, str) else user.user_id

    def error(exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "message": str(exc)})

    @router.get("/api/agent/plugins")
    def plugins(request: Request):
        owner(request)
        return {"code": 200, "plugins": service.plugins()}

    @router.post("/api/cases/{case_id}/agent/attachments")
    def upload(request: Request, case_id: str, file: UploadFile = File(...), modality: str = Form(...)):
        try:
            return {"code": 200, "attachment": service.upload(owner(request), case_id, file.file, file.filename, modality)}
        except AgentToolError as exc:
            return error(exc)

    @router.get("/api/cases/{case_id}/agent/attachments/{attachment_id}")
    def attachment(request: Request, case_id: str, attachment_id: str):
        try:
            path, item = service.attachment_file(owner(request), case_id, attachment_id)
            inline = item["kind"] in {"image", "video"}
            return FileResponse(path, media_type=mimetypes.guess_type(item["filename"])[0] or "application/octet-stream",
                                filename=None if inline else item["filename"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
        except AgentToolError as exc:
            return error(exc)

    @router.post("/api/cases/{case_id}/agent/tool-runs")
    def start(request: Request, case_id: str, payload: ToolRunRequest):
        try:
            run = service.start(owner(request), case_id, payload.message, payload.attachment_ids, payload.plugin_ids, payload.idempotency_key)
            return JSONResponse(status_code=202, content={"code": 202, "run": run})
        except AgentToolError as exc:
            return error(exc)

    @router.get("/api/cases/{case_id}/agent/tool-runs")
    def list_runs(request: Request, case_id: str):
        try:
            return {"code": 200, "runs": service.list(owner(request), case_id)}
        except AgentToolError as exc:
            return error(exc)

    @router.get("/api/cases/{case_id}/agent/tool-runs/{run_id}")
    def get(request: Request, case_id: str, run_id: str):
        try:
            return {"code": 200, "run": service.get(owner(request), case_id, run_id)}
        except AgentToolError as exc:
            return error(exc)

    @router.delete("/api/cases/{case_id}/agent/tool-runs/{run_id}")
    def cancel(request: Request, case_id: str, run_id: str):
        try:
            return {"code": 200, "run": service.cancel(owner(request), case_id, run_id)}
        except AgentToolError as exc:
            return error(exc)

    @router.get("/api/cases/{case_id}/agent/tool-runs/{run_id}/files/{step_id}/{filename}")
    def artifact(request: Request, case_id: str, run_id: str, step_id: str, filename: str):
        try:
            return FileResponse(service.artifact(owner(request), case_id, run_id, step_id, filename),
                                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
        except AgentToolError as exc:
            return error(exc)

    return router
