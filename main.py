import copy
import io
import json
import logging
import math
import mimetypes
import os
import secrets
import sys
import time
import uuid
import base64
import cv2
import shutil
import threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from PIL import Image, UnidentifiedImageError

from model import SpineModel
from pseudoct_generator import PseudoCTGenerator
from ai_report_service import DeepSeekReportService
from mindspine.config import load_settings
from mindspine.auth_sessions import AuthSessions
from mindspine.gait import GaitService, GaitError
from mindspine.gait_api import create_router as create_gait_router
from mindspine.knowledge import KnowledgeIndex
from mindspine.agent_runtime import ClinicalAgent
from mindspine.agent_tools import AgentToolService, AgentToolError, create_router as create_agent_tools_router
from mindspine.auth import (
    ALLOWED_AVATAR_MIME_TYPES,
    MAX_AVATAR_BYTES,
    AccountValidationError,
    UserProfile,
    UserStore,
)
from mindspine.personal_cases import PersonalCaseStore, PersonalCaseValidationError
from mindspine.spine_agent import (
    SpineAgentPermissionError,
    SpineAgentStore,
    SpineAgentValidationError,
    SpineHealthAgent,
    THIRD_PARTY_REPRODUCTION_PLAN,
)
from mindspine.runtime import get_app_root, get_resource_root
from mindspine.sessions import (
    CaseRegistry,
    ModelProxy,
    SessionTokenCodec,
    current_session_id,
    new_session_id,
)

APP_ROOT = get_app_root()
RESOURCE_ROOT = get_resource_root()
WEB_ROOT = os.path.join(RESOURCE_ROOT, "web")
SETTINGS = load_settings(APP_ROOT)
TEMP_DIR = str(SETTINGS.data_dir)
PSEUDOCT_INPUT_DIR = os.path.join(TEMP_DIR, "pseudoct_inputs")
PSEUDOCT_OUTPUT_DIR = os.path.join(TEMP_DIR, "pseudoct_outputs")
BATCH_JOB_ROOT = os.path.join(TEMP_DIR, "batch_jobs")
SESSION_ROOT = os.path.join(TEMP_DIR, "sessions")
USER_DATABASE_PATH = SETTINGS.data_dir / "mindspine_accounts.sqlite3"
PERSONAL_CASE_DATABASE_PATH = SETTINGS.data_dir / "mindspine_personal_cases.sqlite3"
SPINE_AGENT_DATABASE_PATH = SETTINGS.data_dir / "mindspine_spine_agent.sqlite3"
AUTH_COOKIE_NAME = "mindspine_auth"

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(PSEUDOCT_INPUT_DIR, exist_ok=True)
os.makedirs(PSEUDOCT_OUTPUT_DIR, exist_ok=True)
os.makedirs(BATCH_JOB_ROOT, exist_ok=True)
os.makedirs(SESSION_ROOT, exist_ok=True)

logging.basicConfig(
    level=os.getenv("MINDSPINE_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("mindspine.api")

app = FastAPI(
    title="SpineInsight API",
    version="2.0.0",
    docs_url="/docs" if SETTINGS.expose_docs else None,
    redoc_url="/redoc" if SETTINGS.expose_docs else None,
    openapi_url="/openapi.json" if SETTINGS.expose_docs else None,
)

if SETTINGS.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(SETTINGS.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Request-ID"],
    )

if SETTINGS.trusted_hosts and SETTINGS.trusted_hosts != ("*",):
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(SETTINGS.trusted_hosts))

model_template = SpineModel()
status_msg = model_template.load_models()
case_registry = CaseRegistry(
    model_template=model_template,
    ttl_seconds=SETTINGS.session_ttl_seconds,
    max_cases_per_session=SETTINGS.max_cases_per_session,
)
model = ModelProxy(case_registry)
session_codec = SessionTokenCodec(SETTINGS.session_secret)
auth_codec = AuthSessions(SETTINGS.data_dir / 'mindspine_auth_sessions.sqlite3', SETTINGS.session_ttl_seconds)
user_store = UserStore(USER_DATABASE_PATH)
personal_case_store = PersonalCaseStore(PERSONAL_CASE_DATABASE_PATH)

pseudoct_service = PseudoCTGenerator(
    project_root=RESOURCE_ROOT,
    output_root=PSEUDOCT_OUTPUT_DIR,
    bbox_detector=model_template.model_rgb,
    predict_device=model_template.run_device,
    initial_mode="source_direct",
)

ai_report_service = DeepSeekReportService(base_dir=RESOURCE_ROOT)
spine_agent_store = SpineAgentStore(SPINE_AGENT_DATABASE_PATH)
gait_service = GaitService(SETTINGS.data_dir / 'gait')
knowledge_index = KnowledgeIndex(Path(RESOURCE_ROOT) / 'knowledge_sources' / 'clinical_v1_20260908')
from mindspine.exam_history import ExamHistoryProvider
exam_history_provider = ExamHistoryProvider(spine_agent_store, gait_service, personal_case_store)
AGENT_EXECUTION_LOCK = threading.RLock()


def structured_legacy_case_facts(user_id: str, _case_id: str) -> dict:
    """Expose a narrow compatibility digest to the controlled Agent.

    This deliberately excludes free-text notes, filenames, IDs, raw assets and
    storage locations. It lets the Agent acknowledge existing DR/RGB/clinical
    records without confusing a previous product measurement with a new worker
    result or exporting private raw data to an external model.
    """

    spine_agent_store.get_case(user_id, _case_id)
    legacy_case = personal_case_store.get_case(user_id, limit=24, case_id=_case_id,
        include_unassigned=spine_agent_store.is_legacy_case(user_id, _case_id))
    labels = {"dr": "既有 DR / X 光链路", "rgb": "既有体表 RGB 链路", "video": "视频素材", "clinical": "人工临床记录", "checkin": "状态打卡"}
    metric_labels = {
        "LL": "LL", "SS": "SS", "SSA": "SS/SA", "LCA": "LCA", "HARRISON_A": "LCA", "LCI": "LCI",
        "cobb_angle": "Cobb", "atr_angle": "ATR", "pain_score": "疼痛评分", "activity_limit": "活动受限",
    }
    facts = []
    modalities = []
    for entry in legacy_case.get("entries", [])[:12]:
        kind = str(entry.get("kind") or "")
        if kind not in labels:
            continue
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        values = {}
        angles = payload.get("angles") if isinstance(payload.get("angles"), dict) else {}
        for key, label in metric_labels.items():
            candidate = angles.get(key) if key in angles else payload.get(key)
            try:
                number = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and -10000 <= number <= 10000:
                values[label] = round(number, 2)
        facts.append({
            "source": labels[kind],
            "kind": kind,
            "state": str(entry.get("status") or "unknown")[:32],
            "recorded_at": str(entry.get("recorded_at") or "")[:16],
            "measurements": values,
        })
        if labels[kind] not in modalities:
            modalities.append(labels[kind])
    return {"fact_count": len(facts), "modalities": modalities, "facts": facts}


spine_health_agent = ClinicalAgent(
    spine_agent_store,
    knowledge_index,
    gait_service,
    report_service=ai_report_service,
    history_provider=exam_history_provider,
    facts_provider=structured_legacy_case_facts,
)
print(f"DeepSeek 报告增强状态: {ai_report_service.describe()['status']}")
print(f"AI 引擎初始化状态: {status_msg}")
print(f"X光图生成器状态: {pseudoct_service.status}")

MODEL_INFERENCE_LOCK = threading.RLock()
PSEUDOCT_INFERENCE_LOCK = threading.RLock()
BATCH_JOBS: Dict[str, dict] = {}
BATCH_JOBS_LOCK = threading.RLock()
BATCH_EXECUTOR = ThreadPoolExecutor(max_workers=SETTINGS.batch_workers, thread_name_prefix="mindspine-batch")

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
SUPPORTED_XRAY_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | {".dcm"}
SESSION_CLEANUP_LOCK = threading.Lock()
LAST_SESSION_CLEANUP_AT = 0.0

PUBLIC_API_PATHS = {
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
}


def get_authenticated_user(request: Request) -> Optional[UserProfile]:
    token = request.cookies.get(AUTH_COOKIE_NAME, "")
    return user_store.get_user(auth_codec.decode(token))


def auth_error_response() -> JSONResponse:
    return JSONResponse(status_code=401, content={"code": 401, "message": "请先登录后再使用工作台"})


def set_auth_cookie(response: Response, user: UserProfile) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        auth_codec.encode(user.user_id),
        max_age=SETTINGS.session_ttl_seconds,
        httponly=True,
        secure=SETTINGS.session_cookie_secure,
        samesite="lax",
        path="/",
    )


@app.middleware("http")
async def isolate_browser_session(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "").strip()[:96] or uuid.uuid4().hex
    path = request.url.path
    protected_path = request.url.path.startswith(("/api/", "/temp/", "/generated/"))
    if SETTINGS.production and SETTINGS.proxy_secret and protected_path:
        supplied = request.headers.get("x-mindspine-proxy-secret", "")
        if not secrets.compare_digest(supplied, SETTINGS.proxy_secret):
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "请求未通过服务网关认证"},
                headers={"X-Request-ID": request_id},
            )

    if path == "/index1.html":
        return RedirectResponse(url="/", status_code=308)

    user = get_authenticated_user(request)
    request.state.user = user
    public_api = path in PUBLIC_API_PATHS or path.startswith("/api/auth/avatar/")
    if path == "/workspace1.html" and user is not None:
        return RedirectResponse(url="/workspace.html", status_code=308)

    requires_login = path in {"/workspace.html", "/workspace1.html", "/personal-workspace.html", "/personal-case.html", "/health-home.html", "/health-agent.html"} or (
        path == '/gait-workspace.html' or
        (path.startswith("/api/") and not public_api)
        or path.startswith(("/temp/", "/generated/"))
    )
    if requires_login and user is None:
        if path in {"/workspace.html", "/workspace1.html", "/personal-workspace.html", "/personal-case.html", "/health-home.html", "/health-agent.html", '/gait-workspace.html'}:
            return RedirectResponse(url="/login.html", status_code=303)
        return auth_error_response()

    token = request.cookies.get(SETTINGS.session_cookie_name, "")
    session_id = user.user_id if user else session_codec.decode(token)
    created = user is None and session_id is None
    if session_id is None:
        session_id = new_session_id()

    with case_registry.activate_session(session_id):
        response = await call_next(request)

    if created:
        response.set_cookie(
            SETTINGS.session_cookie_name,
            session_codec.encode(session_id),
            max_age=SETTINGS.session_ttl_seconds,
            httponly=True,
            secure=SETTINGS.session_cookie_secure,
            samesite="lax",
            path="/",
        )

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if path in {
        "/personal-workspace.html", "/personal-case.html", "/health-home.html", "/health-agent.html",
        "/personal-app.js", "/personal-case.js", "/health-home.js", "/health-agent.js", "/spine-demo.js",
    }:
        # These pages share the same local case view.  Do not let an embedded
        # browser keep an older HTML/JS pair after a desktop-app restart.
        response.headers["Cache-Control"] = "no-store, max-age=0"
    if SETTINGS.production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    maybe_cleanup_expired_sessions()
    return response


def maybe_cleanup_expired_sessions() -> None:
    global LAST_SESSION_CLEANUP_AT
    now = time.time()
    if now - LAST_SESSION_CLEANUP_AT < 300 or not SESSION_CLEANUP_LOCK.acquire(blocking=False):
        return
    try:
        LAST_SESSION_CLEANUP_AT = now
        for session_id in case_registry.expired_session_ids():
            for root in (SESSION_ROOT, BATCH_JOB_ROOT, PSEUDOCT_INPUT_DIR, PSEUDOCT_OUTPUT_DIR):
                target = os.path.abspath(os.path.join(root, session_id))
                root_abs = os.path.abspath(root)
                if os.path.commonpath([target, root_abs]) == root_abs and os.path.isdir(target):
                    shutil.rmtree(target, ignore_errors=True)
    finally:
        SESSION_CLEANUP_LOCK.release()


def cv2_to_base64(img):
    if img is None:
        return ""
    _, buffer = cv2.imencode(".jpg", img)
    return "data:image/jpeg;base64," + base64.b64encode(buffer).decode("utf-8")


def sanitize_base_name(file_name: str) -> str:
    file_name = file_name or "image"
    base = os.path.splitext(os.path.basename(file_name))[0]
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in base).strip("_")
    return cleaned or "image"


class UploadValidationError(ValueError):
    pass


def session_folder(root: str, *parts: str) -> str:
    folder = os.path.join(root, current_session_id(), *parts)
    os.makedirs(folder, exist_ok=True)
    return folder


def save_upload_file(upload: UploadFile, folder: str, mode: str = "image") -> str:
    os.makedirs(folder, exist_ok=True)
    ext = os.path.splitext(upload.filename or "")[1].lower()
    supported = SUPPORTED_XRAY_EXTENSIONS if mode == "xray" else SUPPORTED_IMAGE_EXTENSIONS
    if ext not in supported:
        raise UploadValidationError(f"不支持的文件格式：{ext or '无扩展名'}")
    safe_name = sanitize_base_name(upload.filename)
    file_path = os.path.join(folder, f"{uuid.uuid4().hex[:8]}_{safe_name}{ext}")
    written = 0
    try:
        with open(file_path, "wb") as buffer:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > SETTINGS.max_upload_bytes:
                    raise UploadValidationError(
                        f"单个文件不能超过 {SETTINGS.max_upload_bytes // (1024 * 1024)} MB"
                    )
                buffer.write(chunk)
        if written == 0:
            raise UploadValidationError("上传文件为空")
    except Exception:
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass
        raise
    return file_path


def generated_url(abs_path: str) -> str:
    output_root_abs = os.path.abspath(PSEUDOCT_OUTPUT_DIR)
    abs_path = os.path.abspath(abs_path)
    if os.path.commonpath([abs_path, output_root_abs]) != output_root_abs:
        raise ValueError("生成文件路径越界")
    rel_path = os.path.relpath(abs_path, output_root_abs).replace("\\", "/")
    if rel_path.split("/", 1)[0] != current_session_id():
        raise ValueError("生成文件不属于当前会话")
    return f"/generated/{rel_path}"


def temp_url(abs_path: str) -> str:
    temp_root_abs = os.path.abspath(TEMP_DIR)
    abs_path = os.path.abspath(abs_path)
    if os.path.commonpath([abs_path, temp_root_abs]) != temp_root_abs:
        raise ValueError("临时文件路径越界")
    rel_path = os.path.relpath(abs_path, temp_root_abs).replace("\\", "/")
    parts = rel_path.split("/")
    owned = (
        len(parts) >= 2
        and (
            (parts[0] in {"batch_jobs", "pseudoct_inputs", "pseudoct_outputs", "sessions"} and parts[1] == current_session_id())
            or parts[0] == current_session_id()
        )
    )
    if not owned:
        raise ValueError("临时文件不属于当前会话")
    return f"/temp/{rel_path}"


def resolve_generated_abs_path(url_path: str) -> str:
    prefix = "/generated/"
    if not url_path or not url_path.startswith(prefix):
        raise ValueError("非法的生成文件地址")

    rel_url = url_path[len(prefix):]
    if rel_url.split("/", 1)[0] != current_session_id():
        raise PermissionError("无权访问其他会话的生成文件")
    rel_path = rel_url.replace("/", os.sep)
    abs_path = os.path.abspath(os.path.join(PSEUDOCT_OUTPUT_DIR, rel_path))
    output_root_abs = os.path.abspath(PSEUDOCT_OUTPUT_DIR)

    if os.path.commonpath([abs_path, output_root_abs]) != output_root_abs:
        raise ValueError("生成文件路径越界")

    if not os.path.exists(abs_path):
        raise FileNotFoundError("生成文件不存在")

    return abs_path


def ensure_bgr_image(image):
    if image is None:
        return None
    if len(image.shape) == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if len(image.shape) == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image.copy()


def write_image_file(path: str, image) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not cv2.imwrite(path, image):
        raise RuntimeError(f"图像写入失败: {os.path.basename(path)}")


def format_orientation_label(raw_label: str) -> str:
    text = str(raw_label or "").strip().lower()
    if not text:
        return "未知朝向"
    if "right" in text or "右" in text:
        return "朝右"
    if "left" in text or "左" in text:
        return "朝左"
    return text


def prepare_xray_ready_image(image_bgr):
    image_bgr = ensure_bgr_image(image_bgr)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype("uint8")
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def route_xray_orientation(image_bgr):
    routed = ensure_bgr_image(image_bgr)
    raw_label = ""
    was_flipped = False

    if model.cls_model_xray is None:
        return routed, raw_label, was_flipped, "未加载 X 光左右分类模型，已跳过自动镜像。"

    try:
        small_img = cv2.resize(routed, (224, 224))
        with MODEL_INFERENCE_LOCK:
            results = model.cls_model_xray.predict(
                source=small_img,
                device=model.run_device,
                verbose=False,
            )
        top1_idx = results[0].probs.top1
        raw_label = str(results[0].names[top1_idx])
        orientation_label = format_orientation_label(raw_label)

        if "右" in orientation_label:
            routed = cv2.flip(routed, 1)
            was_flipped = True
            return routed, raw_label, was_flipped, "X 光左右判断为朝右，已自动镜像为朝左后再测算。"

        return routed, raw_label, was_flipped, "X 光左右判断为朝左，无需镜像，已直接进入测算。"
    except Exception as e:
        return routed, raw_label, was_flipped, f"X 光左右判断异常，已跳过自动镜像: {str(e)}"


def normalize_left_facing_image(image_bgr, mode: str):
    output = ensure_bgr_image(image_bgr)
    raw_label = ""
    was_flipped = False
    orientation_label = "未知朝向"

    cls_model = model.cls_model_xray if mode == "xray" else model.cls_model_rgb
    classify_input = prepare_xray_ready_image(output) if mode == "xray" else output

    if cls_model is None:
        return output, {
            "orientation_label": orientation_label,
            "was_flipped": was_flipped,
            "message": "未加载左右分类模型，已跳过自动镜像。",
        }

    try:
        small_img = cv2.resize(classify_input, (224, 224))
        with MODEL_INFERENCE_LOCK:
            results = cls_model.predict(
                source=small_img,
                device=model.run_device,
                verbose=False,
            )
        top1_idx = results[0].probs.top1
        raw_label = str(results[0].names[top1_idx])
        orientation_label = format_orientation_label(raw_label)

        if "右" in orientation_label:
            output = cv2.flip(output, 1)
            was_flipped = True

        return output, {
            "orientation_label": orientation_label,
            "was_flipped": was_flipped,
            "message": "已统一朝左" if was_flipped else "原图已是朝左",
        }
    except Exception as e:
        return output, {
            "orientation_label": format_orientation_label(raw_label),
            "was_flipped": False,
            "message": f"左右分类异常，已跳过自动镜像: {str(e)}",
        }


def has_valid_xray_detection(result) -> bool:
    if result.boxes is None or len(result.boxes) == 0:
        return False
    if result.keypoints is None:
        return False

    try:
        keypoints = result.keypoints.xy.cpu().numpy()
        if keypoints.size == 0:
            return False
        return bool(np.any(keypoints > 1.0))
    except Exception:
        return True


def predict_xray_candidate(image_bgr, scale: float, conf: float, measurement_overlay: bool = True):
    source = ensure_bgr_image(image_bgr)
    with MODEL_INFERENCE_LOCK:
        results = model.model_xray.predict(
            source=source,
            conf=conf,
            device=model.run_device,
            verbose=False,
        )
    result = results[0]

    annotated = result.plot(boxes=False, labels=False)
    annotated, angles, slips = model.process_medical_image(result, annotated, scale)
    if not measurement_overlay:
        # Agent cards carry calibrated measurements separately. A bare keypoint
        # overlay avoids printing legacy default-scale millimetres on JPEGs.
        annotated = result.plot(boxes=False, labels=False)
    detected = has_valid_xray_detection(result)

    return {
        "detected": detected,
        "annotated": annotated,
        "source": source,
        "angles": angles,
        "slips": slips,
        "raw_result": result,
    }


def compute_foreground_bbox(image_bgr):
    image_bgr = ensure_bgr_image(image_bgr)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    if gray.max() <= 0:
        return (0, 0, w, h)

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask_otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, mask_floor = cv2.threshold(blur, max(10, int(np.percentile(blur, 70) * 0.25)), 255, cv2.THRESH_BINARY)
    mask = cv2.bitwise_or(mask_otsu, mask_floor)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return (0, 0, w, h)

    contour = max(contours, key=cv2.contourArea)
    x, y, bw, bh = cv2.boundingRect(contour)

    if bw * bh < w * h * 0.005:
        return (0, 0, w, h)

    return (x, y, x + bw, y + bh)


def expand_foreground_bbox(bbox, image_shape, pad_x_ratio=0.90, pad_top_ratio=0.22, pad_bottom_ratio=0.38):
    h, w = image_shape[:2]
    x1, y1, x2, y2 = bbox
    bw = max(1, x2 - x1)
    bh = max(1, y2 - y1)

    pad_x = int(round(bw * pad_x_ratio))
    pad_top = int(round(bh * pad_top_ratio))
    pad_bottom = int(round(bh * pad_bottom_ratio))

    x1 = max(0, x1 - pad_x)
    x2 = min(w, x2 + pad_x)
    y1 = max(0, y1 - pad_top)
    y2 = min(h, y2 + pad_bottom)
    return (x1, y1, x2, y2)


def create_halo_layer(mask, sigma=38, gain=0.22):
    halo = cv2.GaussianBlur(mask, (0, 0), sigma)
    halo = np.clip(halo.astype(np.float32) * gain, 0, 72).astype(np.uint8)
    return halo


def rebuild_pseudoct_to_xray_canvas(
    image_bgr,
    canvas_size=(768, 1024),
    occupy_height=0.84,
    occupy_width=0.48,
    halo_gain=0.0,
    blur_sigma=0.0,
):
    image_bgr = ensure_bgr_image(image_bgr)
    bbox = compute_foreground_bbox(image_bgr)
    x1, y1, x2, y2 = expand_foreground_bbox(bbox, image_bgr.shape)

    crop = image_bgr[y1:y2, x1:x2]
    if crop.size == 0:
        crop = image_bgr.copy()

    crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    crop_gray = cv2.normalize(crop_gray, None, 0, 255, cv2.NORM_MINMAX).astype("uint8")

    if blur_sigma > 0:
        crop_gray = cv2.GaussianBlur(crop_gray, (0, 0), blur_sigma)

    canvas_w, canvas_h = canvas_size
    canvas = np.zeros((canvas_h, canvas_w), dtype=np.uint8)

    crop_h, crop_w = crop_gray.shape[:2]
    scale = min((canvas_w * occupy_width) / max(1, crop_w), (canvas_h * occupy_height) / max(1, crop_h))
    scale = max(scale, 1e-3)

    resized_w = max(1, int(round(crop_w * scale)))
    resized_h = max(1, int(round(crop_h * scale)))
    resized = cv2.resize(crop_gray, (resized_w, resized_h), interpolation=cv2.INTER_CUBIC)

    offset_x = max(0, (canvas_w - resized_w) // 2)
    offset_y = max(0, (canvas_h - resized_h) // 2)
    roi = canvas[offset_y:offset_y + resized_h, offset_x:offset_x + resized_w]

    if halo_gain > 0:
        mask = np.where(resized > 10, 255, 0).astype(np.uint8)
        halo = create_halo_layer(mask, sigma=38, gain=halo_gain)
        roi[:] = np.maximum(roi, halo)

    roi[:] = np.maximum(roi, resized)
    return prepare_xray_ready_image(cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR))


def build_pseudoct_xray_candidates(oriented_source_bgr):
    return [
        {
            "label": "闭环适配器A：自动扩边重建",
            "image": rebuild_pseudoct_to_xray_canvas(
                oriented_source_bgr,
                canvas_size=(768, 1024),
                occupy_height=0.84,
                occupy_width=0.48,
                halo_gain=0.00,
                blur_sigma=0.0,
            ),
            "conf": 0.25,
        },
        {
            "label": "闭环适配器B：扩边 + 软背景增强",
            "image": rebuild_pseudoct_to_xray_canvas(
                oriented_source_bgr,
                canvas_size=(768, 1024),
                occupy_height=0.86,
                occupy_width=0.50,
                halo_gain=0.22,
                blur_sigma=0.6,
            ),
            "conf": 0.22,
        },
        {
            "label": "闭环适配器C：更宽视野回退",
            "image": rebuild_pseudoct_to_xray_canvas(
                oriented_source_bgr,
                canvas_size=(896, 1152),
                occupy_height=0.78,
                occupy_width=0.58,
                halo_gain=0.18,
                blur_sigma=1.1,
            ),
            "conf": 0.20,
        },
    ]


def overlay_xray_failure_notice(image_bgr):
    canvas = ensure_bgr_image(image_bgr).copy()
    h, w = canvas.shape[:2]

    x1, y1 = 28, 28
    x2 = min(w - 28, 1120)
    y2 = min(h - 28, 170)

    cv2.rectangle(canvas, (x1, y1), (x2, y2), (18, 18, 18), -1)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 0, 255), 2)

    cv2.putText(
        canvas,
        "No measurable keypoints detected",
        (x1 + 18, y1 + 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.95,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Check X-ray image gap / contrast / anatomy context",
        (x1 + 18, y1 + 95),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (220, 220, 220),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Fallback adapter also failed to detect measurable landmarks",
        (x1 + 18, y1 + 132),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.56,
        (180, 180, 180),
        1,
        cv2.LINE_AA,
    )
    return canvas


def overlay_rgb_failure_notice(image_bgr, text="ROI not detected"):
    canvas = ensure_bgr_image(image_bgr).copy()
    h, w = canvas.shape[:2]
    x1, y1 = 18, 18
    x2 = min(w - 18, 560)
    y2 = min(h - 18, 108)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), (18, 18, 18), -1)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 165, 255), 2)
    cv2.putText(canvas, text, (x1 + 16, y1 + 42), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(canvas, "Batch RGB region detection fallback", (x1 + 16, y1 + 76), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (210, 210, 210), 1, cv2.LINE_AA)
    return canvas


def detect_rgb_region(image_bgr):
    if model.model_rgb is None:
        raise RuntimeError("RGB 模型未加载")

    img = ensure_bgr_image(image_bgr)
    detect_canvas = img.copy()

    with MODEL_INFERENCE_LOCK:
        res = model.model_rgb.predict(
            source=img,
            conf=0.45,
            iou=0.5,
            device=model.run_device,
            verbose=False,
        )

    boxes = res[0].boxes
    best_box = None
    max_c = -1.0

    if len(boxes) > 0:
        for box in boxes:
            conf_val = float(box.conf[0].item())
            if conf_val > max_c:
                max_c = conf_val
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                best_box = (int(x1), int(y1), int(x2), int(y2))

    crop_img = None
    crop_box = []

    if best_box:
        x1, y1, x2, y2 = best_box
        cv2.rectangle(detect_canvas, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"Spine {max_c * 100:.1f}%"
        (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(detect_canvas, (x1, y1 - 25), (x1 + w, y1), (0, 255, 0), -1)
        cv2.putText(detect_canvas, label, (x1, y1 - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        pad = 15
        h_img, w_img = img.shape[:2]
        c_x1, c_y1 = max(0, x1 - pad), max(0, y1 - pad)
        c_x2, c_y2 = min(w_img, x2 + pad), min(h_img, y2 + pad)
        crop_box = [c_x1, c_y1, c_x2, c_y2]
        crop_img = img[c_y1:c_y2, c_x1:c_x2].copy()
    else:
        detect_canvas = overlay_rgb_failure_notice(detect_canvas)

    return {
        "best_box": list(best_box) if best_box else [],
        "confidence": None if best_box is None else max_c,
        "detected_image": detect_canvas,
        "crop_image": crop_img,
        "crop_box": crop_box,
        "has_crop": crop_img is not None,
    }


def load_image_into_xray_state(file_path: str, default_scale: float = 1.5):
    model.current_mode = "xray"
    model.image_path = file_path
    model.is_batch_mode = False

    cv_img, auto_scale, raw_arr, ww, wl = model.read_medical_image_and_scale(file_path, default_scale)
    if cv_img is None:
        raise RuntimeError("图像读取失败")

    cv_img = ensure_bgr_image(cv_img)

    model.current_cv_img = cv_img
    model.raw_pixel_array = raw_arr
    model.current_main_img = cv_img
    model.current_sub_img = None
    model.cv_image_result = None
    model.current_angles = None
    model.current_slip_list = []
    model.last_bbox = None

    return {
        "auto_scale": auto_scale,
        "ww": ww,
        "wl": wl,
    }


def run_current_xray_inference(scale: float, measurement_overlay: bool = True):
    if model.model_xray is None:
        raise RuntimeError("X 光模型未加载")
    if model.current_cv_img is None:
        raise RuntimeError("当前没有可分析的图像")

    raw_source = ensure_bgr_image(model.current_cv_img)
    standard_ready = prepare_xray_ready_image(raw_source)
    routed_standard, raw_orientation_label, was_flipped, routing_message = route_xray_orientation(standard_ready)

    oriented_raw_source = cv2.flip(raw_source, 1) if was_flipped else raw_source.copy()

    standard_result = predict_xray_candidate(routed_standard, scale, conf=0.35, measurement_overlay=measurement_overlay)
    if standard_result["detected"]:
        result_message = f"{routing_message} 标准 X 光直测已检出关键点并完成测算。"
        final = {
            "annotated": standard_result["annotated"],
            "source": standard_result["source"],
            "angles": standard_result["angles"],
            "slips": standard_result["slips"],
            "detected": True,
            "was_flipped": was_flipped,
            "orientation_label": format_orientation_label(raw_orientation_label),
            "message": result_message,
            "pipeline_label": "标准X光直测",
        }
    else:
        candidates = build_pseudoct_xray_candidates(oriented_raw_source)
        fallback_success = None

        for candidate in candidates:
            candidate_result = predict_xray_candidate(candidate["image"], scale, conf=candidate["conf"], measurement_overlay=measurement_overlay)
            if candidate_result["detected"]:
                fallback_success = (candidate, candidate_result)
                break

        if fallback_success is not None:
            candidate, candidate_result = fallback_success
            result_message = (
                f"{routing_message} 标准 X 光直测未检出可测量关键点，"
                f"已切换到X光图闭环专用适配器，并在“{candidate['label']}”阶段成功检出关键点。"
            )
            final = {
                "annotated": candidate_result["annotated"],
                "source": candidate_result["source"],
                "angles": candidate_result["angles"],
                "slips": candidate_result["slips"],
                "detected": True,
                "was_flipped": was_flipped,
                "orientation_label": format_orientation_label(raw_orientation_label),
                "message": result_message,
                "pipeline_label": candidate["label"],
            }
        else:
            failure_source = candidates[0]["image"] if candidates else routed_standard
            annotated = overlay_xray_failure_notice(failure_source)
            result_message = (
                f"{routing_message} 标准 X 光直测未检出可测量关键点，"
                "且X光图闭环专用适配器的多阶段回退也未检出可测量关键点。"
                " 这通常说明当前X光图结果与真实侧位 X 光训练域差距仍然较大。"
            )
            final = {
                "annotated": annotated,
                "source": failure_source,
                "angles": {
                    "LL": None,
                    "SSA": None,
                    "HARRISON_A": None,
                    "LCI": None,
                    "LL_NEAR": None,
                    "LL_FAR": None,
                    "SS": None,
                    "LCA": None,
                },
                "slips": [],
                "detected": False,
                "was_flipped": was_flipped,
                "orientation_label": format_orientation_label(raw_orientation_label),
                "message": result_message,
                "pipeline_label": "闭环适配器失败回退",
            }

    model.cv_image_result = final["annotated"]
    model.current_main_img = final["annotated"]
    model.current_sub_img = final["source"]
    model.current_angles = final["angles"]
    model.current_slip_list = final["slips"]

    return final


def serialize_pseudoct_item(item: dict) -> dict:
    inference_mode = item.get("inference_mode", "transfer")

    if inference_mode == "source_direct":
        display_input_key = "overlay"
        display_input_label = "左半输入图"
        model_input_key = "crop"
    else:
        display_input_key = "crop"
        display_input_label = "ROI输入图"
        model_input_key = "masked"

    return {
        "job_id": item["job_id"],
        "case_id": item["case_id"],
        "filename": item["source_name"],
        "inference_mode": inference_mode,
        "mode_label": item.get("mode_label", ""),
        "source_layout": item.get("source_layout", ""),
        "note": item.get("note", ""),
        "bbox": item.get("bbox", []) or [],
        "crop_box": item.get("crop_box", []) or [],
        "bbox_confidence": item.get("bbox_confidence"),
        "mask_fill_ratio": item.get("mask_fill_ratio"),
        "image_size": item.get("image_size", []),
        "network_input_size": item.get("network_input_size", []),
        "detector_name": item.get("detector_name", ""),
        "checkpoint_name": item.get("checkpoint_name", ""),
        "display_input_label": display_input_label,
        "display_input_url": generated_url(item["paths"][display_input_key]),
        "model_input_url": generated_url(item["paths"][model_input_key]),
        "overlay_url": generated_url(item["paths"]["overlay"]),
        "crop_url": generated_url(item["paths"]["crop"]),
        "masked_url": generated_url(item["paths"]["masked"]),
        "soft_mask_url": generated_url(item["paths"]["soft_mask"]),
        "generated_url": generated_url(item["paths"]["generated"]),
        "mask_url": generated_url(item["paths"]["mask"]),
        "preview_url": generated_url(item["paths"]["preview"]),
    }


def build_report_ai_context():
    angles = model.current_angles or {}
    slips = model.current_slip_list or []

    normalized_angles = {}

    def read_angle(*keys):
        for key in keys:
            value = angles.get(key)
            if value is not None:
                return float(value)
        return None

    normalized_angles["LL"] = read_angle("LL")
    normalized_angles["SSA"] = read_angle("SSA", "SS")
    normalized_angles["HARRISON_A"] = read_angle("HARRISON_A", "LCA")
    normalized_angles["LCI"] = read_angle("LCI")
    normalized_angles["LL_NEAR"] = read_angle("LL_NEAR")
    normalized_angles["LL_FAR"] = read_angle("LL_FAR")
    normalized_angles["SS"] = normalized_angles["SSA"]
    normalized_angles["LCA"] = normalized_angles["HARRISON_A"]

    normalized_slips = []
    for item in slips:
        normalized_slips.append(
            {
                "segment": str(item.get("segment", "--")),
                "distance": float(item.get("distance", 0.0)),
                "is_slip": bool(item.get("is_slip", False)),
                "is_ant": bool(item.get("is_ant", False)),
                "is_retro": bool(item.get("is_retro", False)),
            }
        )

    return {
        "angles": normalized_angles,
        "slips": normalized_slips,
        "current_mode": model.current_mode or "xray",
        "has_measurement": any(v is not None for v in normalized_angles.values()),
    }


def create_batch_job(kind: str, total: int):
    job_id = uuid.uuid4().hex[:12]
    owner_session_id = current_session_id()
    job_dir = os.path.join(BATCH_JOB_ROOT, owner_session_id, job_id)
    os.makedirs(job_dir, exist_ok=True)

    job = {
        "job_id": job_id,
        "owner_session_id": owner_session_id,
        "kind": kind,
        "status": "queued",
        "message": "文件已接收，等待开始处理。",
        "current_filename": "",
        "total": total,
        "processed": 0,
        "progress": 0.0,
        "items": [],
        "errors": [],
        "summary": {"success": 0, "failed": 0},
        "created_at": time.time(),
        "updated_at": time.time(),
        "finished_at": None,
        "job_dir": job_dir,
    }

    with BATCH_JOBS_LOCK:
        BATCH_JOBS[job_id] = job
        persist_batch_job_unlocked(job)
    return job_id, job_dir


def persist_batch_job_unlocked(job: dict) -> None:
    job_dir = job.get("job_dir")
    if not job_dir:
        return
    path = os.path.join(job_dir, "job.json")
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(job, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def batch_title_for_kind(kind: str) -> str:
    if kind == "xray_left":
        return "X 光文件夹批处理"
    if kind == "rgb_pipeline":
        return "RGB 文件夹批处理"
    return "批处理任务"


def patch_batch_job(job_id: str, **kwargs):
    with BATCH_JOBS_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return
        job.update(kwargs)
        job["updated_at"] = time.time()
        persist_batch_job_unlocked(job)


def set_batch_job_progress(job_id: str, processed: int, total: int, current_filename: str = ""):
    progress = 100.0 if total <= 0 else round((processed / total) * 100.0, 1)
    patch_batch_job(
        job_id,
        done=processed,
        processed=processed,
        total=total,
        progress=progress,
        current_label=current_filename,
        current_filename=current_filename,
    )


def push_batch_job_item(job_id: str, item: dict):
    with BATCH_JOBS_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return
        job["items"].append(item)
        if item.get("status") == "failed":
            pass
        else:
            job["summary"]["success"] += 1
        job["updated_at"] = time.time()
        persist_batch_job_unlocked(job)


def push_batch_job_error(job_id: str, filename: str, message: str):
    with BATCH_JOBS_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return
        job["errors"].append({"filename": filename, "message": message})
        job["summary"]["failed"] += 1
        job["updated_at"] = time.time()
        persist_batch_job_unlocked(job)


def get_batch_job_snapshot(job_id: str, owner_session_id: str = None):
    with BATCH_JOBS_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return None
        expected_owner = owner_session_id or current_session_id()
        if job.get("owner_session_id") != expected_owner:
            return None
        snapshot = copy.deepcopy(job)
    snapshot.pop("job_dir", None)
    snapshot.pop("owner_session_id", None)
    snapshot["title"] = batch_title_for_kind(snapshot.get("kind", ""))
    snapshot["done"] = int(snapshot.get("processed", snapshot.get("done", 0)))
    snapshot["current_label"] = snapshot.get("current_filename", snapshot.get("current_label", ""))

    normalized_items = []
    for item in snapshot.get("items", []):
        normalized = dict(item)
        normalized.setdefault("status", "success")

        if snapshot.get("kind") == "xray_left":
            normalized.setdefault("oriented_url", normalized.get("left_url", ""))
            normalized.setdefault("preview_url", normalized.get("left_url", ""))
        elif snapshot.get("kind") == "rgb_pipeline":
            normalized.setdefault(
                "preview_url",
                normalized.get("crop_url") or normalized.get("detected_url") or normalized.get("left_url") or "",
            )

        normalized_items.append(normalized)

    snapshot["items"] = normalized_items
    return snapshot


def restore_batch_jobs() -> None:
    restored = 0
    for metadata_path in Path(BATCH_JOB_ROOT).glob("*/*/job.json"):
        try:
            with metadata_path.open("r", encoding="utf-8") as handle:
                job = json.load(handle)
            job_id = str(job.get("job_id", ""))
            owner = str(job.get("owner_session_id", ""))
            if not job_id or not owner or metadata_path.parent.name != job_id:
                continue
            job["job_dir"] = str(metadata_path.parent)
            if job.get("status") in {"queued", "running"}:
                job.update(
                    status="failed",
                    message="服务重启导致任务中断，请重新提交。",
                    finished_at=time.time(),
                    updated_at=time.time(),
                )
                persist_batch_job_unlocked(job)
            BATCH_JOBS[job_id] = job
            restored += 1
        except Exception as exc:
            LOGGER.warning("Skipping invalid batch metadata %s: %s", metadata_path, exc)
    if restored:
        LOGGER.info("Restored %s batch job records", restored)


restore_batch_jobs()


def is_supported_extension(filename: str, mode: str):
    ext = os.path.splitext(filename or "")[1].lower()
    if mode == "xray":
        return ext in SUPPORTED_XRAY_EXTENSIONS
    return ext in SUPPORTED_IMAGE_EXTENSIONS


def build_batch_prefix(index: int, filename: str):
    return f"{index:03d}_{sanitize_base_name(filename)}"


def run_batch_job_for_session(owner_session_id: str, target, *args) -> None:
    with case_registry.activate_session(owner_session_id):
        worker_case_id = f"batch_{args[0]}" if args else f"batch_{uuid.uuid4().hex}"
        try:
            with case_registry.activate_case(worker_case_id, remember=False, create=True):
                target(*args)
        except Exception:
            LOGGER.exception("Batch worker failed")
            if args:
                patch_batch_job(
                    args[0],
                    status="failed",
                    message="批量任务执行失败，请稍后重试。",
                    finished_at=time.time(),
                )
        finally:
            case_registry.delete_case(worker_case_id)


def active_batch_job_count(owner_session_id: str) -> int:
    with BATCH_JOBS_LOCK:
        return sum(
            1
            for job in BATCH_JOBS.values()
            if job.get("owner_session_id") == owner_session_id and job.get("status") in {"queued", "running"}
        )


def process_xray_batch_left_job(job_id: str, job_dir: str, entries: List[dict]):
    total = len(entries)
    patch_batch_job(job_id, status="running", message="正在批量统一 X 光朝向...")

    left_dir = os.path.join(job_dir, "outputs", "left")

    for idx, entry in enumerate(entries, start=1):
        filename = entry["filename"]
        try:
            cv_img, _, _, _, _ = model.read_medical_image_and_scale(entry["input_path"], 1.5)
            if cv_img is None:
                raise RuntimeError("图像读取失败")

            oriented_img, orientation_meta = normalize_left_facing_image(cv_img, "xray")
            left_name = f"{entry['prefix']}_left.png"
            left_path = os.path.join(left_dir, left_name)
            write_image_file(left_path, oriented_img)

            item = {
                "index": idx - 1,
                "status": "success",
                "filename": filename,
                "orientation_label": orientation_meta["orientation_label"],
                "was_flipped": orientation_meta["was_flipped"],
                "note": orientation_meta["message"],
                "left_name": left_name,
                "left_url": temp_url(left_path),
                "oriented_url": temp_url(left_path),
                "preview_url": temp_url(left_path),
            }
            push_batch_job_item(job_id, item)
        except Exception as e:
            push_batch_job_item(
                job_id,
                {
                    "index": idx - 1,
                    "status": "failed",
                    "filename": filename,
                    "orientation_label": "未知朝向",
                    "was_flipped": False,
                    "note": str(e),
                    "message": str(e),
                    "left_name": "",
                    "left_url": "",
                    "oriented_url": "",
                    "preview_url": "",
                },
            )
            push_batch_job_error(job_id, filename, str(e))
        finally:
            set_batch_job_progress(job_id, idx, total, filename)

    snapshot = get_batch_job_snapshot(job_id) or {}
    patch_batch_job(
        job_id,
        status="completed",
        progress=100.0,
        current_filename="",
        finished_at=time.time(),
        message=f"X光批量朝左处理完成：成功 {snapshot.get('summary', {}).get('success', 0)} 张，失败 {snapshot.get('summary', {}).get('failed', 0)} 张。",
    )


def process_rgb_batch_pipeline_job(job_id: str, job_dir: str, entries: List[dict]):
    total = len(entries)
    patch_batch_job(job_id, status="running", message="正在批量执行 RGB 朝左标准化、区域识别与自动裁剪...")

    left_dir = os.path.join(job_dir, "outputs", "left")
    detect_dir = os.path.join(job_dir, "outputs", "detected")
    crop_dir = os.path.join(job_dir, "outputs", "crop")

    for idx, entry in enumerate(entries, start=1):
        filename = entry["filename"]
        try:
            cv_img, _, _, _, _ = model.read_medical_image_and_scale(entry["input_path"], 1.5)
            if cv_img is None:
                raise RuntimeError("图像读取失败")

            base_img = ensure_bgr_image(cv_img)
            oriented_img, orientation_meta = normalize_left_facing_image(base_img, "rgb")
            detect_info = detect_rgb_region(oriented_img)

            left_name = f"{entry['prefix']}_left.png"
            detected_name = f"{entry['prefix']}_detect.png"
            crop_name = f"{entry['prefix']}_crop.png"

            left_path = os.path.join(left_dir, left_name)
            detected_path = os.path.join(detect_dir, detected_name)

            write_image_file(left_path, oriented_img)
            write_image_file(detected_path, detect_info["detected_image"])

            crop_url = ""
            crop_name_out = ""
            if detect_info["has_crop"]:
                crop_path = os.path.join(crop_dir, crop_name)
                write_image_file(crop_path, detect_info["crop_image"])
                crop_url = temp_url(crop_path)
                crop_name_out = crop_name

            item = {
                "index": idx - 1,
                "status": "success",
                "filename": filename,
                "orientation_label": orientation_meta["orientation_label"],
                "was_flipped": orientation_meta["was_flipped"],
                "note": orientation_meta["message"],
                "bbox": detect_info["best_box"],
                "bbox_confidence": detect_info["confidence"],
                "crop_box": detect_info["crop_box"],
                "has_crop": detect_info["has_crop"],
                "left_name": left_name,
                "left_url": temp_url(left_path),
                "detected_name": detected_name,
                "detected_url": temp_url(detected_path),
                "crop_name": crop_name_out,
                "crop_url": crop_url,
                "preview_url": crop_url or temp_url(detected_path) or temp_url(left_path),
            }
            push_batch_job_item(job_id, item)
        except Exception as e:
            push_batch_job_item(
                job_id,
                {
                    "index": idx - 1,
                    "status": "failed",
                    "filename": filename,
                    "orientation_label": "未知朝向",
                    "was_flipped": False,
                    "note": str(e),
                    "message": str(e),
                    "bbox": [],
                    "bbox_confidence": None,
                    "crop_box": [],
                    "has_crop": False,
                    "left_name": "",
                    "left_url": "",
                    "detected_name": "",
                    "detected_url": "",
                    "crop_name": "",
                    "crop_url": "",
                    "preview_url": "",
                },
            )
            push_batch_job_error(job_id, filename, str(e))
        finally:
            set_batch_job_progress(job_id, idx, total, filename)

    snapshot = get_batch_job_snapshot(job_id) or {}
    patch_batch_job(
        job_id,
        status="completed",
        progress=100.0,
        current_filename="",
        finished_at=time.time(),
        message=f"RGB 批量处理完成：成功 {snapshot.get('summary', {}).get('success', 0)} 张，失败 {snapshot.get('summary', {}).get('failed', 0)} 张。",
    )


@app.post("/api/upload")
def upload_image(file: UploadFile = File(...), mode: str = Form("xray")):
    mode = (mode or "xray").strip().lower()
    if mode not in {"xray", "rgb"}:
        return JSONResponse(status_code=400, content={"code": 400, "message": "无效的影像模式"})

    file_id = uuid.uuid4().hex
    try:
        case_registry.create_case(file_id)
        file_path = save_upload_file(
            file,
            session_folder(SESSION_ROOT, "cases", file_id),
            mode=mode,
        )

        model.current_mode = mode
        model.case_id = file_id
        model.image_path = file_path
        model.is_batch_mode = False

        cv_img, auto_scale, raw_arr, ww, wl = model.read_medical_image_and_scale(file_path, 1.5)
        if cv_img is None:
            raise UploadValidationError("图像读取失败，请检查文件是否完整或 DICOM 编码是否受支持")

        if mode != "xray":
            cv_img, raw_arr = model.auto_flip_if_needed(cv_img, mode, raw_arr)

        model.current_cv_img = cv_img
        model.raw_pixel_array = raw_arr
        model.current_main_img = cv_img
        model.current_sub_img = None
        model.cv_image_result = None
        model.current_angles = None
        model.current_slip_list = []
        model.last_bbox = None

        return JSONResponse(
            {
                "code": 200,
                "id": file_id,
                "filename": file.filename,
                "preview_b64": cv2_to_base64(model.current_main_img),
                "dicom_info": {
                    "ww": ww,
                    "wl": wl,
                    "auto_scale": auto_scale,
                    "min_v": float(raw_arr.min()) if raw_arr is not None else 0,
                    "max_v": float(raw_arr.max()) if raw_arr is not None else 255,
                },
            }
        )
    except UploadValidationError as exc:
        case_registry.delete_case(file_id)
        return JSONResponse(status_code=422, content={"code": 422, "message": str(exc)})
    except Exception:
        case_registry.delete_case(file_id)
        LOGGER.exception("Image upload failed")
        return JSONResponse(status_code=500, content={"code": 500, "message": "影像处理失败，请检查文件后重试"})


class XrayRequest(BaseModel):
    id: str
    scale: float = 1.5


@app.post("/api/xray")
def run_xray(req: XrayRequest):
    try:
        if not math.isfinite(req.scale) or not 0.1 <= req.scale <= 10.0:
            return JSONResponse(status_code=400, content={"code": 400, "message": "比例尺必须在 0.1 到 10.0 之间"})
        case_registry.select_case(req.id)
        result = run_current_xray_inference(req.scale)
        return JSONResponse(
            {
                "code": 200,
                "main_img_b64": cv2_to_base64(result["annotated"]),
                "sub_img_b64": cv2_to_base64(result["source"]),
                "angles": result["angles"],
                "slips": result["slips"],
                "detected": result["detected"],
                "was_flipped": result["was_flipped"],
                "orientation_label": result["orientation_label"],
                "message": result["message"],
                "pipeline_label": result["pipeline_label"],
            }
        )
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "病例不存在或不属于当前会话"})
    except Exception:
        LOGGER.exception("X-ray inference failed")
        return JSONResponse(status_code=500, content={"code": 500, "message": "X 光分析失败，请稍后重试"})


class RgbRequest(BaseModel):
    id: str


@app.post("/api/rgb")
def run_rgb(req: RgbRequest):
    try:
        case_registry.select_case(req.id)
        if model.model_rgb is None:
            return JSONResponse(status_code=503, content={"code": 503, "message": "RGB 模型未加载"})
        if model.current_cv_img is None:
            return JSONResponse(status_code=409, content={"code": 409, "message": "请先上传 RGB 图像"})

        img = ensure_bgr_image(model.current_cv_img.copy())
        detect_info = detect_rgb_region(img)

        model.cv_image_result = detect_info["detected_image"]
        model.current_main_img = detect_info["detected_image"]
        model.current_sub_img = model.current_cv_img
        model.last_bbox = tuple(detect_info["best_box"]) if detect_info["best_box"] else None

        return JSONResponse(
            {
                "code": 200,
                "main_img_b64": cv2_to_base64(model.current_main_img),
                "sub_img_b64": cv2_to_base64(model.current_sub_img),
                "rgb_metrics": {
                    "conf": detect_info["confidence"],
                    "box": detect_info["best_box"],
                } if detect_info["best_box"] else None,
            }
        )
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "病例不存在或不属于当前会话"})
    except Exception:
        LOGGER.exception("RGB inference failed")
        return JSONResponse(status_code=500, content={"code": 500, "message": "RGB 分析失败，请稍后重试"})


class WindowingRequest(BaseModel):
    wl: float
    ww: float
    id: Optional[str] = None


@app.post("/api/windowing")
def apply_windowing(req: WindowingRequest):
    try:
        case_registry.select_case(req.id)
        if not math.isfinite(req.wl) or not math.isfinite(req.ww) or req.ww <= 0:
            return JSONResponse(status_code=400, content={"code": 400, "message": "窗宽窗位参数无效"})
        model.update_windowing(req.wl, req.ww)
        return {"code": 200, "main_img_b64": cv2_to_base64(model.current_main_img)}
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "病例不存在或不属于当前会话"})


class CaseActionRequest(BaseModel):
    id: Optional[str] = None


@app.post("/api/crop")
def crop_image(req: Optional[CaseActionRequest] = None):
    try:
        case_registry.select_case(req.id if req else None)
        if model.last_bbox:
            model.crop_rgb_image()
            return {
                "code": 200,
                "main_img_b64": cv2_to_base64(model.current_main_img),
                "sub_img_b64": cv2_to_base64(model.current_sub_img),
            }
        return JSONResponse(status_code=409, content={"code": 400, "message": "当前没有可裁剪区域"})
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "病例不存在或不属于当前会话"})


@app.get("/api/report_ai/status")
def get_report_ai_status():
    return {"code": 200, "service": ai_report_service.describe()}


@app.get("/api/export_pdf")
def export_pdf(audience: str = "doctor", use_ai: int = 0, id: Optional[str] = None):
    try:
        case_registry.select_case(id)
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "病例不存在或不属于当前会话"})

    audience = (audience or "doctor").strip().lower()
    if audience not in ("doctor", "personal"):
        audience = "doctor"

    use_ai = int(use_ai or 0)
    ai_sections = None

    if audience == "personal" and use_ai == 1:
        ai_sections = ai_report_service.generate_personal_sections(build_report_ai_context())

    if audience == "doctor":
        suffix = "Doctor"
        download_name = "MindSpine_Doctor_Report.pdf"
    else:
        suffix = "PersonalAI" if use_ai == 1 else "Personal"
        download_name = "MindSpine_Personal_AI_Report.pdf" if use_ai == 1 else "MindSpine_Personal_Report.pdf"

    pdf_path = os.path.join(
        session_folder(SESSION_ROOT, "reports"),
        f"Report_{suffix}_{uuid.uuid4().hex[:10]}.pdf",
    )
    try:
        model.export_pdf_report(pdf_path, audience=audience, ai_sections=ai_sections)
    except Exception:
        LOGGER.exception("PDF export failed")
        return JSONResponse(status_code=500, content={"code": 500, "message": "报告生成失败，请稍后重试"})

    return FileResponse(
        path=pdf_path,
        filename=download_name,
        media_type="application/pdf",
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/api/pseudoct/status")
def get_pseudoct_status(mode: str = "transfer"):
    with PSEUDOCT_INFERENCE_LOCK:
        pseudoct_service.set_bbox_detector(model_template.model_rgb)
        pseudoct_service.ensure_mode(mode)
        return {"code": 200, "service": pseudoct_service.describe()}


@app.post("/api/pseudoct/reload")
def reload_pseudoct_model(mode: str = "transfer"):
    with PSEUDOCT_INFERENCE_LOCK:
        pseudoct_service.set_bbox_detector(model_template.model_rgb)
        pseudoct_service.reload(mode)
        return {"code": 200, "service": pseudoct_service.describe()}


@app.post("/api/pseudoct/single")
def generate_pseudoct_single(file: UploadFile = File(...), mode: str = Form("transfer")):
    job_id = uuid.uuid4().hex[:12]
    input_dir = session_folder(PSEUDOCT_INPUT_DIR, job_id)

    try:
        file_path = save_upload_file(file, input_dir, mode="image")
        with PSEUDOCT_INFERENCE_LOCK:
            pseudoct_service.set_bbox_detector(model_template.model_rgb)
            resolved_mode = pseudoct_service.ensure_mode(mode)
            if not pseudoct_service.ready:
                return JSONResponse(
                    status_code=503,
                    content={
                        "code": 503,
                        "message": pseudoct_service.status,
                        "service": pseudoct_service.describe(),
                    },
                )
            result = pseudoct_service.generate_from_path(
                file_path,
                job_id=job_id,
                item_index=0,
                inference_mode=resolved_mode,
                output_namespace=current_session_id(),
            )
        return JSONResponse(
            {
                "code": 200,
                "job_id": job_id,
                "item": serialize_pseudoct_item(result),
                "service": pseudoct_service.describe(),
            }
        )
    except UploadValidationError as exc:
        return JSONResponse(status_code=422, content={"code": 422, "message": str(exc)})
    except Exception:
        LOGGER.exception("Pseudo-CT generation failed")
        return JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "message": "X 光图生成失败，请稍后重试",
                "service": pseudoct_service.describe(),
            },
        )


@app.post("/api/pseudoct/batch")
def generate_pseudoct_batch(files: List[UploadFile] = File(...), mode: str = Form("transfer")):
    if len(files) > SETTINGS.max_batch_files:
        return JSONResponse(
            status_code=413,
            content={"code": 413, "message": f"单次最多处理 {SETTINGS.max_batch_files} 个文件"},
        )
    job_id = uuid.uuid4().hex[:12]
    input_dir = session_folder(PSEUDOCT_INPUT_DIR, job_id)
    items = []
    errors = []

    with PSEUDOCT_INFERENCE_LOCK:
        pseudoct_service.set_bbox_detector(model_template.model_rgb)
        resolved_mode = pseudoct_service.ensure_mode(mode)
        if not pseudoct_service.ready:
            return JSONResponse(
                status_code=503,
                content={"code": 503, "message": pseudoct_service.status, "service": pseudoct_service.describe()},
            )

        for index, upload in enumerate(files):
            try:
                file_path = save_upload_file(upload, input_dir, mode="image")
                result = pseudoct_service.generate_from_path(
                    file_path,
                    job_id=job_id,
                    item_index=index,
                    inference_mode=resolved_mode,
                    output_namespace=current_session_id(),
                )
                items.append(serialize_pseudoct_item(result))
            except Exception as exc:
                errors.append({"filename": upload.filename, "message": str(exc)})

    if not items:
        return JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "message": "批量生成全部失败",
                "errors": errors,
                "service": pseudoct_service.describe(),
            },
        )

    return JSONResponse(
        {
            "code": 200,
            "job_id": job_id,
            "items": items,
            "errors": errors,
            "summary": {
                "total": len(files),
                "success": len(items),
                "failed": len(errors),
            },
            "service": pseudoct_service.describe(),
        }
    )


class PseudoCTLoopRequest(BaseModel):
    generated_url: str
    scale: float = 1.5


@app.post("/api/pseudoct/loop_to_xray")
def pseudoct_loop_to_xray(req: PseudoCTLoopRequest):
    try:
        if not math.isfinite(req.scale) or not 0.1 <= req.scale <= 10.0:
            return JSONResponse(status_code=400, content={"code": 400, "message": "比例尺必须在 0.1 到 10.0 之间"})
        generated_path = resolve_generated_abs_path(req.generated_url)
        image_id = f"pseudoct_loop_{uuid.uuid4().hex}"
        case_registry.create_case(image_id)
        model.case_id = image_id
        load_image_into_xray_state(generated_path, req.scale)
        result = run_current_xray_inference(req.scale)

        return JSONResponse(
            {
                "code": 200,
                "image_id": image_id,
                "source_file": os.path.basename(generated_path),
                "source_generated_url": req.generated_url,
                "scale": req.scale,
                "main_img_b64": cv2_to_base64(result["annotated"]),
                "sub_img_b64": cv2_to_base64(result["source"]),
                "angles": result["angles"],
                "slips": result["slips"],
                "detected": result["detected"],
                "was_flipped": result["was_flipped"],
                "orientation_label": result["orientation_label"],
                "message": result["message"],
                "pipeline_label": result["pipeline_label"],
            }
        )
    except PermissionError:
        return JSONResponse(status_code=403, content={"code": 403, "message": "无权访问该生成文件"})
    except Exception:
        LOGGER.exception("Pseudo-CT loopback failed")
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "X 光图导入检测失败，请稍后重试"},
        )


@app.post("/api/xray/batch/start")
@app.post("/api/xray/batch_left_start")
def start_xray_batch_left(
    files: List[UploadFile] = File(...),
    scale: float = Form(1.5),
    relative_paths: List[str] = Form(None),
):
    if not math.isfinite(scale) or not 0.1 <= scale <= 10.0:
        return JSONResponse(status_code=400, content={"code": 400, "message": "比例尺必须在 0.1 到 10.0 之间"})
    if len(files) > SETTINGS.max_batch_files:
        return JSONResponse(status_code=413, content={"code": 413, "message": f"单次最多处理 {SETTINGS.max_batch_files} 个文件"})
    owner_session_id = current_session_id()
    if active_batch_job_count(owner_session_id) >= SETTINGS.batch_workers:
        return JSONResponse(status_code=429, content={"code": 429, "message": "当前已有批量任务运行，请稍后再试"})

    valid_uploads = [upload for upload in files if is_supported_extension(upload.filename or "", "xray")]
    if not valid_uploads:
        return JSONResponse(status_code=400, content={"code": 400, "message": "未收到可处理的 X 光图片或 DICOM 文件。"})

    job_id, job_dir = create_batch_job("xray_left", len(valid_uploads))
    input_dir = os.path.join(job_dir, "inputs")
    os.makedirs(input_dir, exist_ok=True)

    entries = []
    for index, upload in enumerate(valid_uploads, start=1):
        try:
            input_path = save_upload_file(upload, input_dir, mode="xray")
        except UploadValidationError as exc:
            push_batch_job_error(job_id, upload.filename or f"image_{index}", str(exc))
            continue
        entries.append(
            {
                "filename": upload.filename or os.path.basename(input_path),
                "input_path": input_path,
                "prefix": build_batch_prefix(index, upload.filename or f"image_{index}"),
            }
        )

    if not entries:
        patch_batch_job(job_id, status="failed", message="没有可处理的文件", finished_at=time.time())
        return JSONResponse(status_code=422, content={"code": 422, "message": "没有可处理的文件"})

    BATCH_EXECUTOR.submit(
        run_batch_job_for_session,
        owner_session_id,
        process_xray_batch_left_job,
        job_id,
        job_dir,
        entries,
    )

    return JSONResponse(
        {
            "code": 200,
            "job": get_batch_job_snapshot(job_id),
        }
    )


@app.post("/api/rgb/batch/start")
@app.post("/api/rgb/batch_pipeline_start")
def start_rgb_batch_pipeline(
    files: List[UploadFile] = File(...),
    relative_paths: List[str] = Form(None),
):
    if len(files) > SETTINGS.max_batch_files:
        return JSONResponse(status_code=413, content={"code": 413, "message": f"单次最多处理 {SETTINGS.max_batch_files} 个文件"})
    owner_session_id = current_session_id()
    if active_batch_job_count(owner_session_id) >= SETTINGS.batch_workers:
        return JSONResponse(status_code=429, content={"code": 429, "message": "当前已有批量任务运行，请稍后再试"})

    valid_uploads = [upload for upload in files if is_supported_extension(upload.filename or "", "rgb")]
    if not valid_uploads:
        return JSONResponse(status_code=400, content={"code": 400, "message": "未收到可处理的 RGB 图片文件。"})

    job_id, job_dir = create_batch_job("rgb_pipeline", len(valid_uploads))
    input_dir = os.path.join(job_dir, "inputs")
    os.makedirs(input_dir, exist_ok=True)

    entries = []
    for index, upload in enumerate(valid_uploads, start=1):
        try:
            input_path = save_upload_file(upload, input_dir, mode="image")
        except UploadValidationError as exc:
            push_batch_job_error(job_id, upload.filename or f"image_{index}", str(exc))
            continue
        entries.append(
            {
                "filename": upload.filename or os.path.basename(input_path),
                "input_path": input_path,
                "prefix": build_batch_prefix(index, upload.filename or f"image_{index}"),
            }
        )

    if not entries:
        patch_batch_job(job_id, status="failed", message="没有可处理的文件", finished_at=time.time())
        return JSONResponse(status_code=422, content={"code": 422, "message": "没有可处理的文件"})

    BATCH_EXECUTOR.submit(
        run_batch_job_for_session,
        owner_session_id,
        process_rgb_batch_pipeline_job,
        job_id,
        job_dir,
        entries,
    )

    return JSONResponse(
        {
            "code": 200,
            "job": get_batch_job_snapshot(job_id),
        }
    )


@app.get("/api/batch_jobs/{job_id}")
def get_batch_job(job_id: str):
    snapshot = get_batch_job_snapshot(job_id)
    if snapshot is None:
        return JSONResponse(status_code=404, content={"code": 404, "message": "批量任务不存在"})
    return JSONResponse({"code": 200, "job": snapshot})


def resolve_owned_file(root: str, relative_path: str, kind: str) -> str:
    root_abs = os.path.abspath(root)
    candidate = os.path.abspath(os.path.join(root_abs, relative_path.replace("/", os.sep)))
    if os.path.commonpath([candidate, root_abs]) != root_abs:
        raise PermissionError("文件路径越界")

    parts = os.path.relpath(candidate, root_abs).replace("\\", "/").split("/")
    session_id = current_session_id()
    if kind == "generated":
        owned = len(parts) >= 2 and parts[0] == session_id
    else:
        owned = len(parts) >= 3 and parts[0] in {"batch_jobs", "sessions"} and parts[1] == session_id

    if not owned:
        raise PermissionError("文件不属于当前会话")
    if not os.path.isfile(candidate):
        raise FileNotFoundError(candidate)
    return candidate


@app.get("/generated/{file_path:path}", include_in_schema=False)
def get_generated_file(file_path: str):
    try:
        path = resolve_owned_file(PSEUDOCT_OUTPUT_DIR, file_path, "generated")
        media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, no-store"})
    except PermissionError:
        return JSONResponse(status_code=403, content={"code": 403, "message": "无权访问该文件"})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "文件不存在或已过期"})


@app.get("/temp/{file_path:path}", include_in_schema=False)
def get_temporary_file(file_path: str):
    try:
        path = resolve_owned_file(TEMP_DIR, file_path, "temp")
        media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, no-store"})
    except PermissionError:
        return JSONResponse(status_code=403, content={"code": 403, "message": "无权访问该文件"})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "文件不存在或已过期"})


@app.get("/health/live", include_in_schema=False)
def health_live():
    return {"status": "ok", "service": "spineinsight-api", "version": "2.0.0"}


@app.get("/health/ready", include_in_schema=False)
def health_ready():
    model_ready = model_template.model_xray is not None and model_template.model_rgb is not None
    payload = {
        "status": "ready" if model_ready else "degraded",
        "models": {
            "xray": model_template.model_xray is not None,
            "rgb": model_template.model_rgb is not None,
            "xray_orientation": model_template.cls_model_xray is not None,
            "rgb_orientation": model_template.cls_model_rgb is not None,
            "pseudoct": pseudoct_service.ready,
        },
        "runtime": case_registry.stats(),
    }
    return JSONResponse(status_code=200 if model_ready else 503, content=payload)


@app.get("/api/session")
def get_session_summary():
    return {
        "code": 200,
        "current_case_id": case_registry.current_case(),
        "limits": {
            "max_upload_mb": SETTINGS.max_upload_bytes // (1024 * 1024),
            "max_batch_files": SETTINGS.max_batch_files,
        },
    }


def read_avatar_upload(avatar: Optional[UploadFile]) -> tuple[bytes | None, str | None]:
    if avatar is None or not avatar.filename:
        return None, None
    declared_type = (avatar.content_type or "").lower().split(";", 1)[0].strip()
    if declared_type not in ALLOWED_AVATAR_MIME_TYPES:
        raise AccountValidationError("头像仅支持 JPG、PNG 或 WebP 格式")
    payload = avatar.file.read(MAX_AVATAR_BYTES + 1)
    if len(payload) > MAX_AVATAR_BYTES:
        raise AccountValidationError("头像文件不能超过 2 MB")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
            detected_format = image.format
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise AccountValidationError("头像文件不是有效的图片") from error

    detected_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(detected_format or "")
    if detected_type is None or detected_type != declared_type:
        raise AccountValidationError("头像文件格式与内容不一致")
    return payload, detected_type


@app.post("/api/auth/register")
def register_account(
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    avatar: Optional[UploadFile] = File(None),
):
    if password != password_confirm:
        return JSONResponse(status_code=400, content={"code": 400, "message": "两次输入的密码不一致"})
    try:
        avatar_data, avatar_mime = read_avatar_upload(avatar)
        user = user_store.register(username, password, avatar_data, avatar_mime)
    except AccountValidationError as error:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(error)})

    response = JSONResponse(content={"code": 200, "user": user.as_dict()})
    set_auth_cookie(response, user)
    return response


@app.post("/api/auth/login")
def login_account(username: str = Form(...), password: str = Form(...)):
    user = user_store.authenticate(username, password)
    if user is None:
        return JSONResponse(status_code=401, content={"code": 401, "message": "用户名或密码不正确"})
    response = JSONResponse(content={"code": 200, "user": user.as_dict()})
    set_auth_cookie(response, user)
    return response


@app.post("/api/auth/logout")
def logout_account(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME, '')
    user_id = auth_codec.decode(token)
    if user_id:
        gait_service.cancel_user(user_id)
    auth_codec.revoke(token)
    response = JSONResponse(content={"code": 200, "message": "已退出当前账号"})
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(SETTINGS.session_cookie_name, path="/")
    return response


@app.get("/api/auth/me")
def get_current_account(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        return auth_error_response()
    return {"code": 200, "user": user.as_dict()}


@app.get("/api/auth/avatar/{user_id}", include_in_schema=False)
def get_account_avatar(user_id: str):
    avatar = user_store.get_avatar(user_id)
    if avatar is None:
        return JSONResponse(status_code=404, content={"code": 404, "message": "头像不存在"})
    payload, mime_type = avatar
    return Response(content=payload, media_type=mime_type, headers={"Cache-Control": "private, max-age=3600"})


class PersonalCaseProfileRequest(BaseModel):
    display_name: str = ""
    birth_date: Optional[str] = None
    sex: str = "undisclosed"
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    clinician_note: str = ""


class PersonalCaseEntryRequest(BaseModel):
    case_id: Optional[str] = None
    kind: str
    status: str = "completed"
    recorded_at: Optional[str] = None
    title: str = ""
    summary: str = ""
    source_filename: str = ""
    payload: Optional[Dict[str, Any]] = None


def current_personal_case_user(request: Request) -> UserProfile:
    user = getattr(request.state, "user", None)
    if user is None:
        raise RuntimeError("当前账号不存在")
    return user


@app.get("/api/personal-case")
def get_personal_case(request: Request, limit: int = 120, case_id: Optional[str] = None):
    user = current_personal_case_user(request)
    if case_id:
        try: spine_agent_store.get_case(user.user_id, case_id)
        except (SpineAgentValidationError, SpineAgentPermissionError) as error: return spine_agent_error_response(error)
    data = personal_case_store.get_case(
        user.user_id,
        default_name=user.username,
        limit=max(1, min(limit, 500)),
        case_id=case_id,
        include_unassigned=bool(case_id and spine_agent_store.is_legacy_case(user.user_id, case_id)),
    )
    return {"code": 200, "case": data}


@app.put("/api/personal-case/profile")
def update_personal_case_profile(request: Request, payload: PersonalCaseProfileRequest):
    user = current_personal_case_user(request)
    try:
        profile = personal_case_store.update_profile(user.user_id, payload.dict())
    except PersonalCaseValidationError as error:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(error)})
    return {"code": 200, "profile": profile.as_dict()}


@app.post("/api/personal-case/entries")
def create_personal_case_entry(request: Request, payload: PersonalCaseEntryRequest):
    user = current_personal_case_user(request)
    try:
        case_id = payload.case_id or spine_agent_store.ensure_default_case(user.user_id)['id']
        spine_agent_store.require_write(user.user_id, case_id)
        entry = personal_case_store.create_entry(user.user_id, {**payload.dict(), 'case_id': case_id})
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    except PersonalCaseValidationError as error:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(error)})
    return {"code": 200, "entry": entry}


@app.delete("/api/personal-case/entries/{entry_id}")
def delete_personal_case_entry(request: Request, entry_id: str):
    user = current_personal_case_user(request)
    if not personal_case_store.delete_entry(user.user_id, entry_id):
        return JSONResponse(status_code=404, content={"code": 404, "message": "记录不存在或不属于当前病例"})
    return {"code": 200, "message": "记录已删除"}


# ---------------------------------------------------------------------------
# Spine Health Agent API
# ---------------------------------------------------------------------------
# These endpoints intentionally live beside the legacy personal-case API.  The
# new case ledger only references existing account data; it never deletes or
# rewrites a historic personal_case_entries row.


class SpineCaseCreateRequest(BaseModel):
    title: str = "我的脊柱健康档案"
    care_scope: str = "spine_exam"


class SpineCaseInviteRequest(BaseModel):
    user_id: str
    role: str
    expires_at: Optional[int] = None


class SpineConsentRequest(BaseModel):
    external_ai_allowed: bool


class SpineQuestionnaireRequest(BaseModel):
    answers: Dict[str, Any]
    protocol_version: str = "spine-intake-v2"


class SpineAgentMessageRequest(BaseModel):
    message: str
    web_search: bool = False


class SpineActionConfirmationRequest(BaseModel):
    case_id: str
    confirmation_token: str


class SpineReviewResolveRequest(BaseModel):
    case_id: str


def current_spine_agent_user(request: Request) -> UserProfile:
    user = getattr(request.state, "user", None)
    if user is None:
        raise RuntimeError("当前账号不存在")
    return user


def spine_agent_error_response(error: Exception) -> JSONResponse:
    if isinstance(error, SpineAgentPermissionError):
        return JSONResponse(status_code=403, content={"code": 403, "message": str(error)})
    return JSONResponse(status_code=400, content={"code": 400, "message": str(error) or "脊柱健康工作台请求无效"})


def spine_case_workspace_payload(user: UserProfile, case_id: str) -> dict:
    workspace = spine_agent_store.workspace(user.user_id, case_id)
    # The legacy case store remains the source of truth for previous entries;
    # exposing it here performs a non-destructive compatibility migration.
    owner_id = workspace['case']['owner_user_id']
    legacy_case = personal_case_store.get_case(owner_id, limit=120,
        case_id=case_id, include_unassigned=spine_agent_store.is_legacy_case(owner_id, case_id))
    workspace["legacy_timeline"] = legacy_case["entries"]
    workspace["legacy_overview"] = legacy_case["overview"]
    workspace['gait_jobs'] = gait_service.list(owner_id, case_id)
    workspace['knowledge'] = knowledge_index.stats()
    workspace['report_url'] = f'/api/cases/{case_id}/report.pdf'
    workspace['agent_tool_runs'] = agent_tool_service.list(owner_id, case_id)
    for action in workspace.get('actions', []):
        linked = action.get('result', {}).get('gait_job_id')
        if linked:
            try:
                job = gait_service.get(owner_id, linked)
                action['state'] = job['status']
                action['result']['gait_job'] = job
            except GaitError:
                pass
    return workspace


@app.get("/api/cases")
def list_spine_cases(request: Request):
    user = current_spine_agent_user(request)
    return {"code": 200, "cases": spine_agent_store.list_cases(user.user_id)}


@app.post("/api/cases")
def create_spine_case(request: Request, payload: SpineCaseCreateRequest):
    user = current_spine_agent_user(request)
    try:
        case = spine_agent_store.create_case(user.user_id, payload.title, payload.care_scope)
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    return {"code": 200, "case": case}


@app.get("/api/cases/default/workspace")
def get_default_spine_case_workspace(request: Request):
    user = current_spine_agent_user(request)
    try:
        case = spine_agent_store.ensure_default_case(user.user_id)
        return {"code": 200, "workspace": spine_case_workspace_payload(user, case["id"])}
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)


@app.get("/api/cases/{case_id}/workspace")
def get_spine_case_workspace(request: Request, case_id: str):
    user = current_spine_agent_user(request)
    try:
        return {"code": 200, "workspace": spine_case_workspace_payload(user, case_id)}
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)


@app.get("/api/cases/{case_id}/timeline")
def get_spine_case_timeline(request: Request, case_id: str):
    user = current_spine_agent_user(request)
    try:
        workspace = spine_case_workspace_payload(user, case_id)
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    return {
        "code": 200,
        "case": workspace["case"],
        "legacy_entries": workspace["legacy_timeline"],
        "assessments": workspace["assessments"],
        "questionnaire": workspace["questionnaire"],
        "review_tasks": workspace["review_tasks"],
    }


@app.post("/api/cases/{case_id}/members")
def invite_spine_case_member(request: Request, case_id: str, payload: SpineCaseInviteRequest):
    user = current_spine_agent_user(request)
    if user_store.get_user(payload.user_id) is None:
        return JSONResponse(status_code=404, content={"code": 404, "message": "受邀账号不存在"})
    try:
        membership = spine_agent_store.invite_member(user.user_id, case_id, payload.user_id, payload.role, payload.expires_at)
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    return {"code": 200, "membership": membership}


@app.put("/api/cases/{case_id}/consent")
def update_spine_case_consent(request: Request, case_id: str, payload: SpineConsentRequest):
    user = current_spine_agent_user(request)
    try:
        consent = spine_agent_store.set_consent(user.user_id, case_id, payload.external_ai_allowed)
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    return {"code": 200, "consent": consent}


@app.get('/api/questionnaire/schema')
def get_spine_questionnaire_schema(request: Request):
    from mindspine.questionnaire import questionnaire_schema
    current_spine_agent_user(request)
    return {'code': 200, 'schema': questionnaire_schema()}


@app.post("/api/cases/{case_id}/questionnaire")
def submit_spine_questionnaire(request: Request, case_id: str, payload: SpineQuestionnaireRequest):
    user = current_spine_agent_user(request)
    try:
        questionnaire = spine_agent_store.submit_questionnaire(user.user_id, case_id, payload.answers, payload.protocol_version)
        triage = questionnaire.get('triage', {})
        spine_agent_store.append_agent_message(user.user_id, case_id, 'assistant',
            '问卷已保存到当前病例。\n' + (triage.get('analysis') or triage.get('message', '安全分流已更新。')),
            audit={'answer_mode': 'workflow', 'questionnaire_id': questionnaire['id'],
                   'result_cards': [{'type': 'questionnaire', 'title': '查看或更新本次问卷'}]})
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    return {"code": 200, "questionnaire": questionnaire}


@app.post("/api/cases/{case_id}/agent/messages")
def send_spine_agent_message(request: Request, case_id: str, payload: SpineAgentMessageRequest):
    user = current_spine_agent_user(request)
    try:
        response = spine_health_agent.respond(user.user_id, case_id, payload.message, web_search=payload.web_search)
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    return {"code": 200, "agent": response}


@app.post("/api/agent-actions/{action_id}/confirm")
def confirm_spine_agent_action(request: Request, action_id: str, payload: SpineActionConfirmationRequest):
    user = current_spine_agent_user(request)
    try:
        with AGENT_EXECUTION_LOCK:
            action = spine_agent_store.confirm_action(user.user_id, payload.case_id, action_id, payload.confirmation_token)
            source_job = action.get('payload', {}).get('source_job_id')
            if action['kind'] == 'run_video_screening' and source_job and not action.get('result', {}).get('gait_job_id') and action['state'] in {'confirmed', 'queued', 'needs_review'}:
                source = gait_service.get(user.user_id, source_job)
                if source['case_id'] != payload.case_id:
                    raise SpineAgentPermissionError('视频不属于当前检查')
                job = gait_service.rerun(user.user_id, source_job, {}, idempotency_key=action_id)
                result = {'gait_job_id': job['id'], 'gait_job': job, 'url': f"gait-workspace.html?case_id={payload.case_id}&job_id={job['id']}"}
                spine_agent_store.update_action_result(user.user_id, payload.case_id, action_id, 'queued', result)
                action.update(state='queued', result=result)
    except GaitError as error:
        return JSONResponse(status_code=error.status_code, content={'code': error.status_code, 'message': str(error)})
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    return {"code": 200, "action": action}


@app.get("/api/jobs/{job_id}")
def get_spine_agent_job(request: Request, job_id: str, case_id: str):
    user = current_spine_agent_user(request)
    try:
        jobs = spine_agent_store.list_assessments(user.user_id, case_id, limit=100)
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    job = next((item for item in jobs if item["id"] == job_id), None)
    if job is None:
        return JSONResponse(status_code=404, content={"code": 404, "message": "任务不存在或不属于当前病例"})
    return {"code": 200, "job": job}


@app.post("/api/review-tasks/{task_id}/resolve")
def resolve_spine_review_task(request: Request, task_id: str, payload: SpineReviewResolveRequest):
    user = current_spine_agent_user(request)
    try:
        resolved = spine_agent_store.resolve_review_task(user.user_id, payload.case_id, task_id)
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)
    if not resolved:
        return JSONResponse(status_code=404, content={"code": 404, "message": "复核任务不存在、已关闭或不属于当前病例"})
    return {"code": 200, "message": "复核任务已关闭"}


@app.get("/api/spine-agent/knowledge-status")
def get_spine_agent_knowledge_status(request: Request):
    current_spine_agent_user(request)
    return {"code": 200, "knowledge": knowledge_index.stats()}


@app.get("/api/spine-agent/third-party")
def get_spine_agent_third_party_status(request: Request):
    current_spine_agent_user(request)
    return {"code": 200, "projects": list(THIRD_PARTY_REPRODUCTION_PLAN)}


@app.post('/api/agent-actions/{action_id}/refresh-confirmation')
def refresh_agent_confirmation(request: Request, action_id: str, payload: SpineReviewResolveRequest):
    user = current_spine_agent_user(request)
    try:
        return {'code': 200, **spine_agent_store.refresh_confirmation(user.user_id, payload.case_id, action_id)}
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)


@app.get('/api/knowledge/search')
def search_clinical_knowledge(request: Request, q: str, audience: str = 'all', limit: int = 6):
    current_spine_agent_user(request)
    return {'code': 200, 'evidence': knowledge_index.search(q[:1600], audience=audience, limit=min(limit,10)), 'knowledge': knowledge_index.stats()}


@app.get('/api/cases/{case_id}/report.pdf')
def export_comprehensive_report(request: Request, case_id: str):
    from mindspine.case_report import build_case_pdf
    user = current_spine_agent_user(request)
    try:
        workspace = spine_case_workspace_payload(user, case_id)
        if 'export' not in workspace['case'].get('scopes', []):
            raise SpineAgentPermissionError('当前授权不允许导出此病例')
        selected = gait_service.selected_result(user.user_id, case_id)
        report, sheet = None, None
        if selected:
            report = selected
            try: sheet = gait_service.artifact(user.user_id, selected['job_id'], 'contact_sheet.jpg')
            except GaitError: pass
        personal = personal_case_store.get_case(workspace['case']['owner_user_id'])
        pdf = build_case_pdf(workspace['case'], workspace.get('questionnaire'), report,
            profile=personal.get('profile'), legacy_entries=workspace.get('legacy_timeline'),
            review=selected.get('review', {}) if selected else {}, contact_sheet=sheet)
        return Response(pdf, media_type='application/pdf', headers={'Content-Disposition': f'inline; filename="SpineInsight-{case_id[:12]}.pdf"', 'Cache-Control':'private, no-store'})
    except (SpineAgentValidationError, SpineAgentPermissionError) as error:
        return spine_agent_error_response(error)


from mindspine.report_api import create_router as create_report_router
app.include_router(create_report_router(
    root=SETTINGS.data_dir / 'case_reports', store=spine_agent_store,
    current_user=current_spine_agent_user, workspace_provider=spine_case_workspace_payload,
    profile_provider=lambda case: personal_case_store.get_case(case['owner_user_id']).get('profile', {}),
))


def agent_plugin_availability() -> dict:
    gait = gait_service.health()
    # Availability must check the requested generator, not the ROI detector or
    # whichever mode was last selected in the separate imaging workspace.
    with PSEUDOCT_INFERENCE_LOCK:
        try:
            pseudoct_service.ensure_mode('source_direct')
            direct = pseudoct_service.describe()
            pseudo_dr = {'available': bool(direct['ready']), 'reason': direct['status']}
        except Exception:
            LOGGER.warning('Agent Pix2Pix source model could not be loaded')
            pseudo_dr = {'available': False, 'reason': 'Pix2Pix 左半图直推权重加载失败，请检查源模型权重'}
    return {
        'keypoints': {'available': model_template.model_xray is not None, 'reason': '侧位 DR 关键点模型已加载' if model_template.model_xray is not None else '侧位 DR 关键点模型未加载'},
        'roi': {'available': model_template.model_rgb is not None, 'reason': '体表 ROI 模型已加载' if model_template.model_rgb is not None else '体表 ROI 模型未加载'},
        'gait': {'available': gait['available'], 'reason': gait['message']},
        'pseudo_dr': pseudo_dr,
    }


def execute_agent_plugin(owner, case_id, run_id, step, attachment, input_path, output, cancelled):
    """Reuse real model pipelines in a fresh mutable case for each tool step."""
    plugin = step['plugin_id']
    images = []

    def image_artifact(name, label, image=None, source=None):
        destination = output / name
        if source is not None:
            shutil.copyfile(source, destination)
        else:
            write_image_file(str(destination), image)
        images.append({'url': AgentToolService.artifact_url(case_id, run_id, step['id'], name), 'label': label})

    if plugin == 'gait':
        try:
            with input_path.open('rb') as stream:
                job = gait_service.create(owner, case_id, stream, attachment['filename'], {}, idempotency_key=step['id'])
            deadline = time.monotonic() + gait_service.timeout_seconds + 120
            while job['status'] not in {'completed', 'rejected', 'failed', 'cancelled'}:
                if cancelled.wait(0.5):
                    gait_service.cancel(owner, job['id'])
                    raise AgentToolError('步态分析已取消', 409)
                if time.monotonic() > deadline:
                    gait_service.cancel(owner, job['id'])
                    raise AgentToolError('步态分析超时，请重试较短视频', 408)
                # Recheck revocable case authorization throughout a long-running job.
                spine_agent_store.require_write(owner, case_id)
                job = gait_service.get(owner, job['id'])
            if job['status'] not in {'completed', 'rejected'}:
                raise AgentToolError(job['message'] or '步态分析未完成', 422)
            report = gait_service.report(owner, job['id'])
            files = report.get('file_urls', {})
            if 'contact_sheet.jpg' in files:
                image_artifact('gait-contact-sheet.jpg', '步态剪影', source=gait_service.artifact(owner, job['id'], 'contact_sheet.jpg'))
            return {'type': 'video', 'title': '步态视频分析', 'images': images,
                    'video_url': files.get('overlay.mp4') or files.get('input_preview.mp4'),
                    'report_url': files.get('report.html'), 'gait_job_id': job['id'],
                    'metrics': {'quality': report.get('quality', {}), 'analysis': report.get('analysis', {})},
                    'summary': job['message'], 'status': 'needs_review' if job['status'] == 'rejected' else 'completed',
                    'model_version': f"ScoNet {job['engine_version']}"}
        except GaitError as error:
            raise AgentToolError(str(error), error.status_code) from None
        except PermissionError:
            if 'job' in locals():
                gait_service.cancel(owner, job['id'])
            raise AgentToolError('病例授权已失效，步态任务已停止', 403) from None

    if plugin == 'pseudo_dr':
        # Source-direct has its own training input protocol: no RGB orientation
        # routing, ROI crop or transfer-mode soft mask may precede this call.
        if cancelled.is_set():
            raise AgentToolError('分析已取消', 409)
        with PSEUDOCT_INFERENCE_LOCK:
            resolved_mode = pseudoct_service.ensure_mode('source_direct')
            if not pseudoct_service.ready:
                raise AgentToolError('Pix2Pix 左半图直推模型未加载', 503)
            result = pseudoct_service.generate_from_path(str(input_path), job_id=step['id'], item_index=0,
                                                        inference_mode=resolved_mode, output_namespace=owner)
        image_artifact('source-input.png', '左半输入图', source=result['paths']['overlay'])
        image_artifact('pseudo-dr.png', '生成类 DR（非真实 X 光）', source=result['paths']['generated'])
        image_artifact('generation-preview.png', 'Pix2Pix 直推预览', source=result['paths']['preview'])
        return {'type': 'image', 'title': '脊柱伪 DR · Pix2Pix 左半图直推', 'images': images,
                'metrics': {}, 'inference_mode': result['inference_mode'],
                'mode_label': result['mode_label'], 'source_layout': result['source_layout'],
                'network_input_size': result['network_input_size'],
                'summary': f"已使用 Pix2Pix 左半图直推模型生成。{result['note']} 这是模型生成结果，不能替代真实 X 光或用于确诊。",
                'status': 'completed', 'model_version': result['checkpoint_name'], 'generated_image': True}

    runtime_case_id = f"agent_{step['id']}"
    with case_registry.activate_session(owner):
        with case_registry.activate_case(runtime_case_id, remember=False, create=True):
            try:
                with MODEL_INFERENCE_LOCK:
                    loaded = load_image_into_xray_state(str(input_path), 1.5)
                    if cancelled.is_set():
                        raise AgentToolError('分析已取消', 409)
                    if plugin == 'keypoints':
                        from mindspine.agent_tools_imaging import calibration_for_image
                        calibration = calibration_for_image(input_path, model.current_cv_img.shape[:2])
                        if calibration['resample_size']:
                            model.current_cv_img = cv2.resize(model.current_cv_img, tuple(calibration['resample_size']), interpolation=cv2.INTER_LINEAR)
                        result = run_current_xray_inference(calibration['pixels_per_mm'] or 1.0, measurement_overlay=False)
                        # Legacy fallback adapters rescale the input canvas. Their
                        # physical scale is not propagated, so retain angles only.
                        calibrated = calibration['calibrated'] and result['pipeline_label'] == '标准X光直测'
                        calibration['calibrated'] = bool(calibrated)
                        if not calibrated:
                            calibration['reason'] = '缺少有效像素标定，或分析适配器改变了图像尺度；不输出毫米距离。'
                        metrics = dict(result['angles'])
                        if not calibrated:
                            metrics.pop('LCI', None)
                        summary = result['message'] if result['detected'] else '未检出可测量关键点，请核对是否为清晰的腰椎侧位影像。'
                        if not calibrated:
                            summary += ' 当前影像未标定，不提供 LCI 或滑脱的毫米值。'
                        image_artifact('annotated.png', '关键点与测量标注', image=result['annotated'])
                        image_artifact('source.png', '分析输入影像', image=result['source'])
                        return {'type': 'image', 'title': 'DR 关键点与角度分析', 'images': images,
                                'metrics': metrics, 'slips': result['slips'] if calibrated else [],
                                'metric_units': {key: 'mm' if key == 'LCI' else '°' for key in metrics},
                                'summary': summary, 'calibration': calibration,
                                'status': 'completed' if result['detected'] else 'needs_review',
                                'model_version': 'best.pt', 'pipeline_label': result['pipeline_label']}
                    if plugin != 'roi':
                        raise AgentToolError('不支持的分析插件', 422)
                    model.current_mode = 'rgb'
                    source, _ = model.auto_flip_if_needed(model.current_cv_img, 'rgb')
                    detection = detect_rgb_region(source)
                    image_artifact('roi.png', '体表 ROI 标注', image=detection['detected_image'])
                    if detection['crop_image'] is not None:
                        image_artifact('roi-crop.png', 'ROI 裁剪', image=detection['crop_image'])
                    return {'type': 'image', 'title': '体表 ROI 识别',
                            'images': images, 'metrics': {'confidence': detection['confidence'], 'box': detection['best_box']},
                            'summary': '已检测并标注体表区域。' if detection['best_box'] else '未检出可靠的体表 ROI，请重新提供清晰体表照片。',
                            'status': 'completed' if detection['best_box'] else 'needs_review', 'model_version': 'bbox_best.pt'}
            finally:
                case_registry.delete_case(runtime_case_id)


def archive_agent_plugin_result(owner, case_id, run_id, attachment, card):
    plugin = card['plugin_id']
    kind = 'dr' if plugin == 'keypoints' else 'video' if plugin == 'gait' else 'rgb'
    payload = {'image_id': f"agent_{run_id}_{attachment['id']}_{plugin}", 'agent_tool_run_id': run_id,
               'attachment_id': attachment['id'], 'tool': plugin, 'model_version': card.get('model_version'),
               'metrics': card.get('metrics', {}), 'media': card.get('images', []), 'review_status': 'unreviewed'}
    if card.get('calibration'):
        payload['calibration'] = card['calibration']
    if plugin == 'pseudo_dr':
        payload.update(inference_mode=card['inference_mode'], source_layout=card['source_layout'],
                       generated_image=True, network_input_size=card['network_input_size'])
    if plugin == 'keypoints':
        payload['angles'] = card['metrics']
    if plugin == 'gait':
        payload['gait_job_id'] = card['gait_job_id']
        # Large video details remain in the dedicated gait report, not the legacy ledger.
        payload['metrics'] = {}
    entry = personal_case_store.create_entry(owner, {
        'case_id': case_id, 'kind': kind, 'status': 'completed' if card['status'] == 'completed' else 'no_measurement',
        'title': card['title'], 'summary': card['summary'][:600], 'source_filename': attachment['filename'], 'payload': payload})
    return entry['id']


agent_tool_service = AgentToolService(SETTINGS.data_dir / 'agent_tools', spine_agent_store,
                                    execute_agent_plugin, agent_plugin_availability,
                                    max_upload_bytes=SETTINGS.max_upload_bytes,
                                    max_video_bytes=gait_service.max_upload_bytes,
                                    record_result=archive_agent_plugin_result)
app.include_router(create_agent_tools_router(agent_tool_service, current_spine_agent_user))
app.include_router(create_gait_router(gait_service, current_spine_agent_user, spine_agent_store.require_write))


@app.on_event('shutdown')
def stop_local_workers():
    agent_tool_service.close()
    gait_service.close()
    BATCH_EXECUTOR.shutdown(wait=False, cancel_futures=True)


app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    local_port = int(os.getenv('MINDSPINE_PORT', '8000'))
    print(f"\n服务启动成功，请在浏览器访问: http://localhost:{local_port}\n")
    uvicorn.run(app, host=os.getenv('MINDSPINE_HOST', '127.0.0.1'), port=local_port)
