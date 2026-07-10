import copy
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
from typing import Dict, List, Optional

from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from model import SpineModel
from pseudoct_generator import PseudoCTGenerator
from ai_report_service import DeepSeekReportService
from mindspine.config import load_settings
from mindspine.sessions import (
    CaseRegistry,
    ModelProxy,
    SessionTokenCodec,
    current_session_id,
    new_session_id,
)


def get_runtime_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_ROOT = get_runtime_base_dir()
WEB_ROOT = os.path.join(APP_ROOT, "web")
SETTINGS = load_settings(APP_ROOT)
TEMP_DIR = str(SETTINGS.data_dir)
PSEUDOCT_INPUT_DIR = os.path.join(TEMP_DIR, "pseudoct_inputs")
PSEUDOCT_OUTPUT_DIR = os.path.join(TEMP_DIR, "pseudoct_outputs")
BATCH_JOB_ROOT = os.path.join(TEMP_DIR, "batch_jobs")
SESSION_ROOT = os.path.join(TEMP_DIR, "sessions")

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
        allow_methods=["GET", "POST", "OPTIONS"],
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

pseudoct_service = PseudoCTGenerator(
    project_root=APP_ROOT,
    output_root=PSEUDOCT_OUTPUT_DIR,
    bbox_detector=model_template.model_rgb,
    predict_device=model_template.run_device,
)

ai_report_service = DeepSeekReportService()
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


@app.middleware("http")
async def isolate_browser_session(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "").strip()[:96] or uuid.uuid4().hex
    protected_path = request.url.path.startswith(("/api/", "/temp/", "/generated/"))
    if SETTINGS.production and SETTINGS.proxy_secret and protected_path:
        supplied = request.headers.get("x-mindspine-proxy-secret", "")
        if not secrets.compare_digest(supplied, SETTINGS.proxy_secret):
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "请求未通过服务网关认证"},
                headers={"X-Request-ID": request_id},
            )
    token = request.cookies.get(SETTINGS.session_cookie_name, "")
    session_id = session_codec.decode(token)
    created = session_id is None
    if created:
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


def predict_xray_candidate(image_bgr, scale: float, conf: float):
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


def run_current_xray_inference(scale: float):
    if model.model_xray is None:
        raise RuntimeError("X 光模型未加载")
    if model.current_cv_img is None:
        raise RuntimeError("当前没有可分析的图像")

    raw_source = ensure_bgr_image(model.current_cv_img)
    standard_ready = prepare_xray_ready_image(raw_source)
    routed_standard, raw_orientation_label, was_flipped, routing_message = route_xray_orientation(standard_ready)

    oriented_raw_source = cv2.flip(raw_source, 1) if was_flipped else raw_source.copy()

    standard_result = predict_xray_candidate(routed_standard, scale, conf=0.35)
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
            candidate_result = predict_xray_candidate(candidate["image"], scale, conf=candidate["conf"])
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


app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    print("\n服务启动成功，请在浏览器访问: http://127.0.0.1:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
