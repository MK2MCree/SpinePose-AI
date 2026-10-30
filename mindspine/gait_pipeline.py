"""Application-owned orchestration around the immutable ScoNet 2.0.1 kernels.

Detection is shared, but silhouettes, quality gates and scores never cross track
boundaries. A track number identifies a trajectory, not a diagnosed person.
"""
from __future__ import annotations

from contextlib import ExitStack
from html import escape
import hashlib
import json
import math
from pathlib import Path
import time
import zipfile

ADAPTER_VERSION = "mindspine-gait-v2"
CACHE_VERSION = 1
MAX_TRACKS = 16


class GaitRuntimeError(RuntimeError):
    """Safe worker error contract: diagnostics never contain inputs or local paths."""

    def __init__(self, public_message, runtime=None):
        super().__init__(public_message)
        self.public_message = public_message
        self.runtime = dict(runtime or {})
        self.diagnostics = self.runtime


class GPUUnavailableError(GaitRuntimeError):
    """An actionable, safe error: never includes private file paths."""


class GaitInputError(GaitRuntimeError, ValueError):
    """A fixed, public instruction for correcting user-selected inputs."""


def resolve_device(requested="auto", allow_cpu_fallback=False, *, torch_module=None, nms=None):
    """Probe actual CUDA kernels freshly, retry initialization once, never silently fall back."""
    if requested not in {"auto", "cpu", "cuda"}:
        raise ValueError("不支持的推理设备参数。")
    if requested == "cpu":
        return "cpu", {"device": "cpu", "actual_device": "cpu", "requested_device": "cpu",
                       "cpu_opt_in": True, "fallback_reason": None, "probe_attempts": 0}
    import importlib
    torch = torch_module or importlib.import_module("torch")
    details = {"requested_device": requested, "torch_version": str(torch.__version__),
               "cuda_runtime": torch.version.cuda, "probe_attempts": 0, "probe_errors": []}
    for attempt in range(2):
        details["probe_attempts"] = attempt + 1
        try:
            if not torch.cuda.is_available():
                raise GPUUnavailableError("cuda_unavailable")
            # Initialize before querying device properties (the old probe did the reverse).
            torch.cuda.init()
            with torch.inference_mode():
                sample = torch.ones((1, 1, 16, 16), device="cuda")
                torch.nn.functional.conv2d(sample, torch.ones((2, 1, 3, 3), device="cuda")).sum().item()
                actual_nms = nms or importlib.import_module("torchvision").ops.nms
                actual_nms(torch.tensor([[0., 0., 3., 3.]], device="cuda"),
                           torch.tensor([1.], device="cuda"), .5).cpu()
                torch.cuda.synchronize()
            details.update(device="cuda", actual_device="cuda", fallback_reason=None, cpu_opt_in=False)
            # Display-only device-name failures must not disable working CUDA kernels.
            try:
                details["gpu_name"] = torch.cuda.get_device_name(0)
            except Exception as exc:
                details["gpu_name"] = "NVIDIA CUDA"
                details["device_name_warning"] = type(exc).__name__
            return "cuda", details
        except Exception as exc:
            details["probe_errors"].append(type(exc).__name__)
    reason = "GPU 计算自检失败（" + "/".join(details["probe_errors"]) + "）"
    if requested == "auto" and allow_cpu_fallback is True:
        details.update(device="cpu", actual_device="cpu", fallback_reason=reason, cpu_opt_in=True)
        return "cpu", details
    details.update(error_code="cuda_preflight_failed", actual_device=None, fallback_reason=reason)
    raise GPUUnavailableError(reason + "。已在人体检测前停止，避免耗时几十分钟。请检查 CUDA 环境后重试，或明确选择 CPU 运行。", details)


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _cache_key(path, model_dir, start, end, fps, upstream):
    return {"source_sha256": _digest(path), "start_seconds": start, "end_seconds": end,
            "analysis_fps": fps, "engine_version": "2.0.1", "adapter_version": ADAPTER_VERSION,
            "frontend_manifest_sha256": _digest(Path(model_dir) / "frontend_manifest.json"),
            "checkpoint_sha256": upstream.MODEL_SHA256,
            "tracking_protocol": "bytetrack-15fps-yolox-fp32-decoded-timestamps-v1"}


def _safe_file(root, name, limit):
    candidate = root / name
    if candidate.is_symlink() or not candidate.is_file() or candidate.resolve().parent != root:
        return None
    if candidate.stat().st_size > limit:
        return None
    return candidate


def _validate_records(records, info, start, end):
    if not isinstance(records, list) or not 0 < len(records) <= 720:
        return False
    previous_frame, previous_time = -1, -1.
    for item in records:
        if not isinstance(item, dict):
            return False
        frame, timestamp = item.get("source_frame"), item.get("timestamp")
        if (isinstance(frame, bool) or not isinstance(frame, int) or not previous_frame < frame < info["frame_count"] or
                not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool) or
                not math.isfinite(timestamp) or timestamp <= previous_time or not start - .001 <= timestamp < end):
            return False
        previous_frame, previous_time = frame, timestamp
        tracks = item.get("tracks")
        if not isinstance(tracks, list) or len(tracks) > 100:
            return False
        ids = set()
        for track in tracks:
            if not isinstance(track, dict):
                return False
            ident, box, score = track.get("track_id"), track.get("bbox_xyxy"), track.get("score")
            if isinstance(ident, bool) or not isinstance(ident, int) or not 0 <= ident <= 1000000 or ident in ids:
                return False
            ids.add(ident)
            if not isinstance(box, list) or len(box) != 4 or not all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box):
                return False
            if not (0 <= box[0] < box[2] <= info["width"] and 0 <= box[1] < box[3] <= info["height"]):
                return False
            if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                return False
    return True


def load_tracking_cache(cache_dir, key, info):
    """The caller must authorize cache_dir; files are data, never executable/pickle."""
    if not cache_dir:
        return None
    try:
        root_path = Path(cache_dir)
        if root_path.is_symlink():
            return None
        root = root_path.resolve()
        manifest_path = _safe_file(root, "tracking_cache.json", 128 * 1024)
        tracks_path = _safe_file(root, "tracks.json", 32 * 1024 * 1024)
        if manifest_path is None or tracks_path is None:
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("version") != CACHE_VERSION or manifest.get("key") != key or
                manifest.get("tracks_sha256") != _digest(tracks_path)):
            return None
        records = json.loads(tracks_path.read_text(encoding="utf-8"))
        return records if _validate_records(records, info, key["start_seconds"], key["end_seconds"]) else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _write_cache(out, records, key):
    _json(out / "tracks.json", records)
    _json(out / "tracking_cache.json", {"version": CACHE_VERSION, "key": key,
                                       "tracks_sha256": _digest(out / "tracks.json")})


def _preview(upstream, path, out, info, start, end, records, progress):
    import cv2
    scale = min(1., 960. / max(info["width"], info["height"]))
    size = (int(info["width"] * scale), int(info["height"] * scale))
    images = []
    with ExitStack() as stack:
        raw = upstream.VideoWriter(out / "input_preview.mp4", size, min(upstream.ANALYSIS_FPS, info["fps"]))
        stack.callback(raw.close)
        overlay = upstream.VideoWriter(out / "overlay.mp4", size, min(upstream.ANALYSIS_FPS, info["fps"]))
        stack.callback(overlay.close)
        count = 0
        for index, (source_index, timestamp, frame, _) in enumerate(upstream.sampled_frames(path, info, start, end)):
            _check_frame(records, index, source_index, timestamp)
            raw.write(frame)
            shown = frame.copy()
            for track in records[index]["tracks"]:
                x1, y1, x2, y2 = [round(v) for v in track["bbox_xyxy"]]
                cv2.rectangle(shown, (x1, y1), (x2, y2), (230, 165, 40), 2)
                cv2.putText(shown, f"ID {track['track_id']}", (max(x1, 0), max(y1 - 8, 25)),
                            cv2.FONT_HERSHEY_SIMPLEX, max(.7, info["height"] / 1100), (230, 165, 40), 2)
            overlay.write(shown)
            if index % max(1, len(records) // 8) == 0 and len(images) < 8:
                images.append((cv2.resize(shown, size), None, timestamp))
            count += 1
        if count != len(records):
            raise RuntimeError("视频解码帧数变化，已停止以避免轨迹错配。")
    upstream._contact_sheet(out / "contact_sheet.jpg", images)


def _check_frame(records, index, source_index, timestamp):
    if (index >= len(records) or records[index]["source_frame"] != source_index or
            abs(records[index]["timestamp"] - timestamp) > .001):
        raise RuntimeError("视频解码帧映射不一致，已停止以避免轨迹错配。")


def _subject(upstream, frontend, classifier, path, out, info, start, end, records, tracks,
             target_id, demo, progress, subject_index, subject_count, save_artifacts):
    """Keep upstream alignment, overlap rejection and quality thresholds unchanged."""
    import cv2
    import numpy as np
    frame_records, masks = [], []
    sheet_images = []
    preview_scale = min(1., 960. / max(info["width"], info["height"]))
    preview_size = (int(info["width"] * preview_scale), int(info["height"] * preview_scale))
    elapsed = time.perf_counter()
    with ExitStack() as stack:
        mask_writer, overlay_writer = None, None
        if save_artifacts:
            mask_writer = upstream.VideoWriter(out / "masks.mp4", (256, 256), min(upstream.ANALYSIS_FPS, info["fps"]))
            stack.callback(mask_writer.close)
            overlay_writer = upstream.VideoWriter(out / "overlay.mp4", preview_size, min(upstream.ANALYSIS_FPS, info["fps"]))
            stack.callback(overlay_writer.close)
        for index, (source_index, timestamp, frame, _) in enumerate(upstream.sampled_frames(path, info, start, end)):
            _check_frame(records, index, source_index, timestamp)
            record = records[index]
            selected = next((track for track in record["tracks"] if track["track_id"] == target_id), None)
            item = dict(source_frame=source_index, timestamp=timestamp, track_id=target_id,
                        accepted_index=None, reason="target_missing", bbox_xyxy=None)
            aligned = None
            overlay = frame.copy() if save_artifacts else None
            if selected:
                box = selected["bbox_xyxy"]
                mask = frontend.segment(frame, box)
                if np.asarray(mask).shape != frame.shape[:2]:
                    raise RuntimeError("分割输出没有正确映射回原视频尺寸。")
                native = getattr(frontend, "last_native_mask", None)
                aligned, quality = upstream.align_silhouette_frame(mask if native is None else native, source_index, len(masks))
                item.update(quality.to_dict())
                item.update(source_frame=source_index, timestamp=timestamp, bbox_xyxy=box,
                            alignment_source="native_192_square_mask" if native is not None else "full_frame_mask")
                crop = (getattr(frontend, "last_segmentation", None) or {}).get("crop_xyxy", box)
                for other in record["tracks"]:
                    if other["track_id"] == target_id:
                        continue
                    ox1, oy1, ox2, oy2 = other["bbox_xyxy"]
                    area = max(0, min(crop[2], ox2) - max(crop[0], ox1)) * max(0, min(crop[3], oy2) - max(crop[1], oy1))
                    if area / max((ox2 - ox1) * (oy2 - oy1), 1) > .10:
                        aligned = None
                        item.update(accepted_index=None, valid=False, reason="overlapping_person")
                        break
                x1, y1, x2, y2 = box
                item["border_touch"] = x1 <= 2 or y1 <= 2 or x2 >= info["width"] - 3 or y2 >= info["height"] - 3
                if aligned is not None:
                    masks.append(aligned)
                if overlay is not None:
                    foreground = np.asarray(mask) > 127
                    overlay[foreground] = (overlay[foreground].astype(np.float32) * .58 + np.array([75, 200, 60]) * .42).astype(np.uint8)
            frame_records.append(item)
            if mask_writer:
                shown = aligned if aligned is not None else np.zeros((64, 64), dtype=np.uint8)
                mask_writer.write(cv2.cvtColor(shown, cv2.COLOR_GRAY2BGR))
                for track in record["tracks"]:
                    x1, y1, x2, y2 = [round(v) for v in track["bbox_xyxy"]]
                    color = (230, 165, 40) if track["track_id"] == target_id else (170, 170, 170)
                    cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(overlay, f"ID {track['track_id']}", (max(0, x1), max(25, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, max(.7, info["height"] / 1100), color, 2)
                overlay_writer.write(overlay)
                if index % max(1, len(records) // 12) == 0 and len(sheet_images) < 12:
                    sheet_images.append((cv2.resize(overlay, preview_size), aligned, timestamp))
            if index % 10 == 0:
                progress("segmenting", f"人物 ID {target_id}：已提取 {len(masks)} 个有效轮廓（{subject_index + 1}/{subject_count}）",
                         .50 + .37 * (subject_index + (index + 1) / len(records)) / subject_count)
    if len(frame_records) != len(records):
        raise RuntimeError("视频第二次解码提前结束，已停止分析。")
    sequence = np.stack(masks) if masks else np.zeros((0, 64, 64), dtype=np.uint8)
    quality = upstream.quality_summary(info, records, frame_records, sequence, target_id, tracks, True, demo)
    segmentation_seconds = time.perf_counter() - elapsed
    elapsed = time.perf_counter()
    prediction = None
    # Research mode no longer bypasses input-quality gates. Scores are not diagnoses.
    if quality["passed"]:
        progress("classifying", f"正在计算人物 ID {target_id} 的独立三类模型分数", .50 + .37 * (subject_index + 1) / subject_count)
        prediction = classifier.predict(sequence)
    if save_artifacts:
        np.save(out / "silhouettes.npy", sequence, allow_pickle=False)
        _json(out / "frame_map.json", frame_records)
        upstream._contact_sheet(out / "contact_sheet.jpg", sheet_images)
    return {"track_id": target_id, "status": "completed" if quality["passed"] else "rejected",
            "prediction": prediction if not demo else None,
            "raw_model_prediction": prediction if demo else None,
            "research_demo": demo, "quality": quality,
            "timings_seconds": {"segmentation": round(segmentation_seconds, 3),
                                "classification": round(time.perf_counter() - elapsed, 3)},
            "clinical_diagnosis_produced": False}


def decorate_analysis(report):
    """Called after the upstream report-view builder as well as before serialization."""
    analysis = report.setdefault("analysis", {})
    if report.get("requires_target_selection"):
        report["summary"] = "人体跟踪已完成，请选择被测者；已保留跟踪缓存，无需重复检测。"
        analysis.update(mode="target_selection_required", title="请选择被测者", summary=report["summary"], model_scores={})
    elif report.get("multi_subject"):
        subjects = report.get("subject_results") or []
        completed = sum(subject.get("status") == "completed" for subject in subjects)
        report["summary"] = f"已分别处理 {len(subjects)} 条人体轨迹，其中 {completed} 条通过输入质控并生成模型分数。各轨迹结果不合并为个人诊断。"
        analysis.update(mode="multi_subject", title="逐人分析结果", summary=report["summary"], model_scores={})
    return report


def render_report_html(report, upstream_renderer):
    """Preserve upstream report, adding explicit selection/multi-subject sections."""
    if not report.get("multi_subject") and not report.get("requires_target_selection"):
        return upstream_renderer(report)
    decorate_analysis(report)
    block = '<section><h2>' + escape(report["analysis"]["title"]) + '</h2><p>' + escape(report["summary"]) + '</p>'
    block += '<ul>' + ''.join('<li>' + escape(str(i.get("message", ""))) + '</li>'
                             for i in (report.get("quality") or {}).get("issues", [])) + '</ul>'
    for subject in report.get("subject_results") or []:
        quality = subject.get("quality") or {}
        research = subject.get("research_demo") is True
        score = subject.get("prediction") or (subject.get("raw_model_prediction") if research else None) or {}
        block += '<h3>人物 ID ' + escape(str(subject["track_id"])) + '</h3>'
        block += '<p>' + ('已生成模型分数（非诊断）' if subject["status"] == "completed" else '未通过输入质控，无分类结论') + '</p>'
        if research:
            block += '<p>研究演示模式：以下为原始模型输出，不是正式筛查结论。</p>'
        labels = {"negative": "阴性类", "neutral": "中间类", "positive": "阳性类"}
        for label, value in (score.get("probabilities") or {}).items():
            block += '<p>' + escape(labels.get(label, str(label))) + ': ' + escape(f'{value * 100:.2f}%') + '</p>'
        block += '<ul>' + ''.join('<li>' + escape(str(i.get("message", ""))) + '</li>' for i in quality.get("issues", [])) + '</ul>'
    block += '</section>'
    runtime = report.get("runtime") or {}
    block += '<section><h2>运行记录</h2><p>设备：' + escape(str(runtime.get("gpu_name") or runtime.get("actual_device", "未记录"))) + '</p>'
    block += '<p>耗时：' + escape(str(report.get("elapsed_seconds", "未记录"))) + ' 秒；跟踪缓存：' + ('已复用' if (report.get("tracking") or {}).get("cache_reused") else '本次计算') + '</p></section>'
    model = report.get("model") or {}
    provenance = [
        ("任务编号", report.get("job_id", "独立分析")),
        ("模型", model.get("model_name") or model.get("name", "ScoNet-MT")),
        ("原始权重 SHA256", model.get("checkpoint_sha256", "未记录")),
        ("报告版本", report.get("report_view_version", "2.0.1")),
        ("适配器版本", runtime.get("adapter_version", ADAPTER_VERSION)),
        ("原始报告 SHA256", report.get("original_report_sha256", "见版本化报告")),
    ]
    block += '<section><h2>模型与溯源记录</h2><dl>' + ''.join(
        '<dt>' + escape(title) + '</dt><dd>' + escape(str(value)) + '</dd>'
        for title, value in provenance) + '</dl></section>'
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>脊见智诊 · 步态分析报告</title><style>body{font:15px/1.8 "Microsoft YaHei",sans-serif;background:#f2f4f8;color:#263044;margin:0}'
            'main{max-width:900px;margin:24px auto;background:white;padding:36px}section{border-bottom:1px solid #ddd;padding:18px 0}'
            'h3{margin-top:32px}li{margin:5px 0}dd{overflow-wrap:anywhere;font-size:12px}dt{font-weight:600}@media print{body{background:white}main{margin:0;padding:0}section{break-inside:avoid}}</style>'
            '<main><h1>脊见智诊 · 步态分析报告</h1>' + block + '<footer>人物编号仅在本视频内有效。模型分数不是诊断或个人患病概率；不同人物的结果不得合并到一个人的健康档案。</footer></main></html>')


def analyze_video(video_path, output_dir, model_dir, progress_callback=None, options=None, cache_dir=None):
    from sconet_video import pipeline as upstream
    from sconet_video.interpretation import build_analysis, render_report_html as upstream_renderer
    options = dict(options or {})
    mode = options.get("analysis_mode", "single")
    if mode not in {"single", "all"}:
        raise ValueError("不支持的人物分析模式。")
    if mode == "all" and options.get("target_track_id") is not None:
        raise ValueError("逐人分析不能同时指定单个人物 ID。")
    started = time.perf_counter()
    timings = {}
    runtime = {"requested_device": options.get("device", "auto"), "actual_device": None,
               "stage_timings_seconds": timings}
    path, out = Path(video_path).resolve(), Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in upstream.GENERATED_FILES | {"tracking_cache.json"}):
        raise ValueError("输出目录已有处理产物，请使用新的任务目录。")
    if path.parent == out:
        raise ValueError("输入视频与处理产物必须存放在独立目录。")
    def progress(stage, message, value):
        if progress_callback:
            runtime["stage_timings_seconds"] = timings
            progress_callback(dict(stage=stage, message=message, progress=round(float(value), 4), runtime=dict(runtime)))
    info = upstream.probe_video(path)
    start = float(options.get("start_seconds") or 0)
    end = float(info["duration_seconds"] if options.get("end_seconds") is None else options["end_seconds"])
    if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= info["duration_seconds"] + .1:
        raise GaitInputError("视频起止时间无效，请选择原视频内的起止位置。")
    if end - start > upstream.MAX_CLIP_SECONDS + .01:
        raise GaitInputError("一次最多处理 45 秒，请明确选择分析片段。")
    progress("gpu_check", "正在自检 CUDA 计算能力；GPU 不可用时不会静默执行漫长的 CPU 推理", .005)
    t = time.perf_counter()
    device, runtime = resolve_device(options.get("device", "auto"), options.get("allow_cpu_fallback", False))
    timings["device_check"] = round(time.perf_counter() - t, 3)
    runtime.update(detector_device=device, classifier_device=device, segmenter_device="cpu")
    progress("loading", "正在校验并加载检测、分割和 ScoNet 权重（" + device.upper() + "）", .01)
    t = time.perf_counter()
    frontend, classifier = upstream._models(model_dir, device)
    timings["model_loading"] = round(time.perf_counter() - t, 3)
    key = _cache_key(path, model_dir, start, end, min(upstream.ANALYSIS_FPS, info["fps"]), upstream)
    t = time.perf_counter()
    records = load_tracking_cache(cache_dir, key, info)
    reused = records is not None
    if records is None:
        records = []
        progress("tracking", "正在检测人体并建立连续轨迹（" + device.upper() + "）", .04)
        for index, timestamp, frame, fallback in upstream.sampled_frames(path, info, start, end):
            tracked = frontend.track(frontend.detect(frame), frame.shape[:2])
            tracks = [dict(track_id=int(v["track_id"]), bbox_xyxy=[float(x) for x in v["bbox_xyxy"]], score=float(v["score"])) for v in tracked]
            records.append(dict(source_frame=index, timestamp=timestamp, timestamp_fallback=fallback, tracks=tracks))
            if len(records) % 5 == 0:
                progress("tracking", f"已跟踪 {len(records)} 帧（{device.upper()}）", .04 + .40 * (timestamp - start) / (end - start))
    else:
        progress("tracking", "已核对视频与模型版本，复用已完成的人体跟踪", .44)
    if not _validate_records(records, info, start, end):
        raise GaitInputError("没有有效且可复核的视频帧与人体跟踪记录，请检查视频或重新选择片段。")
    _write_cache(out, records, key)
    timings["tracking"] = round(time.perf_counter() - t, 3)
    tracks = upstream.summarize_tracks(records, info["height"])
    requested = options.get("target_track_id")
    if requested is not None and requested not in [v["track_id"] for v in tracks]:
        raise GaitInputError("没有找到指定轨迹。若调整了视频片段，请恢复自动选择，重新查看跟踪预览中的人物 ID。")
    ambiguous = mode == "single" and requested is None and len(tracks) > 1 and tracks[1]["rank_score"] >= tracks[0]["rank_score"] * .8
    t = time.perf_counter()
    progress("preview", "正在生成带人物编号的跟踪预览", .46)
    _preview(upstream, path, out, info, start, end, records, progress)
    timings["preview"] = round(time.perf_counter() - t, 3)
    target = requested if requested is not None else (tracks[0]["track_id"] if tracks and mode == "single" and not ambiguous else None)
    demo = bool(options.get("research_demo"))
    subjects = []
    if ambiguous or not tracks:
        quality = {"passed": False, "sampled_frames": len(records), "valid_frames": 0, "usable_coverage": 0,
                   "gate_version": "engineering-v1-not-clinically-validated", "issues": [
                       {"code": "ambiguous_target" if ambiguous else "no_person", "severity": "error",
                        "message": "已检测到多条同样显著的人体轨迹。请选择被测者，或选择逐人分析；不会重新检测。" if ambiguous else "未建立有效人体轨迹。"}]}
    else:
        selected = [target] if mode == "single" else [v["track_id"] for v in tracks]
        if len(selected) > MAX_TRACKS:
            raise GaitInputError("本片段轨迹过多，请先选择单个人物或缩短片段。")
        for index, ident in enumerate(selected):
            subjects.append(_subject(upstream, frontend, classifier, path, out, info, start, end, records, tracks,
                                     ident, demo, progress, index, len(selected), mode == "single"))
        quality = subjects[0]["quality"] if mode == "single" else {
            "passed": all(v["quality"]["passed"] for v in subjects), "sampled_frames": len(records),
            "gate_version": "per-track-engineering-v1-not-clinically-validated", "issues": [],
            "subjects_passed": sum(v["quality"]["passed"] for v in subjects), "subjects_total": len(subjects)}
    status = "completed" if subjects and (mode == "all" or quality["passed"]) else "rejected"
    model = classifier.metadata()
    model.setdefault("name", upstream.MODEL_NAME)
    model.setdefault("checkpoint_sha256", upstream.MODEL_SHA256)
    for stage in ("segmentation", "classification"):
        timings[stage] = round(sum(v["timings_seconds"][stage] for v in subjects), 3)
    runtime.update(stage_timings_seconds=timings, adapter_version=ADAPTER_VERSION,
                   detector_device=device, classifier_device=device, segmenter_device="cpu",
                   tracking_cache_reused=reused)
    report = dict(schema_version="sconet_video_app_v1", status=status, summary="视频处理完成。",
                  research_demo=demo, prediction=subjects[0]["prediction"] if subjects and mode == "single" else None,
                  raw_model_prediction=subjects[0]["raw_model_prediction"] if subjects and mode == "single" else None,
                  quality=quality, model=model, frontend=frontend.metadata(), runtime=runtime,
                  requires_target_selection=ambiguous, multi_subject=mode == "all", subject_results=subjects,
                  source=dict(filename=path.name, sha256=key["source_sha256"], video=info, clip_start_seconds=start, clip_end_seconds=end, ground_truth=None),
                  tracking=dict(selected_track_id=target, selection="all" if mode == "all" else "required" if ambiguous else "explicit" if requested is not None else "longest_visible_person",
                                tracks=tracks, cache_reused=reused),
                  preprocessing=dict(analysis_fps=min(upstream.ANALYSIS_FPS, info["fps"]), time_sampling="decoded_timestamp_no_duplicated_frames",
                                     mask_size=[64, 64], classifier_frame_size=[64, 44], horizontal_crop_each_side=10,
                                     normalization_divisor=255, temporal_aggregation="maximum", maximum_clip_seconds=upstream.MAX_CLIP_SECONDS),
                  preview=dict(codec="H.264", audio=False, frame_count=len(records), fps=min(upstream.ANALYSIS_FPS, info["fps"])),
                  artifacts={"input_preview": "input_preview.mp4", "overlay": "overlay.mp4", "tracks": "tracks.json", "contact_sheet": "contact_sheet.jpg",
                             "report": "report.json", "printable": "report.html", "bundle": "report.zip"},
                  elapsed_seconds=round(time.perf_counter() - started, 3),
                  interpretation="三类分数为研究模型输出，非诊断、非个人疾病概率；本模块不输出 Cobb 角。多人物结果不能合并为个人健康结论。")
    for name in ("masks.mp4", "frame_map.json", "silhouettes.npy"):
        if (out / name).is_file():
            report["artifacts"][name.split(".")[0]] = name
    report["analysis"] = build_analysis(report)
    decorate_analysis(report)
    progress("reporting", "正在保存每条轨迹的质量检查、运行设备与模型结果", .96)
    _json(out / "report.json", report)
    (out / "report.html").write_text(render_report_html(report, upstream_renderer), encoding="utf-8")
    with zipfile.ZipFile(out / "report.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in report["artifacts"].values():
            item = out / name
            if name != "report.zip" and item.is_file() and not item.is_symlink():
                archive.write(item, name)
    progress("completed", report["summary"], 1.)
    return report
