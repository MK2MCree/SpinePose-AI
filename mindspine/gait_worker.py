"""One isolated ScoNet V2.0.1 inference job; invoked by GaitService only.

The parent assigns a Windows kill-on-close Job Object before releasing the
stdin handshake. ffmpeg children consequently share the task's lifetime.
This adapter never imports the standalone application's HTTP server.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def atomic_json(path: Path, document: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, type=Path)
    args = parser.parse_args()
    # Do not import GPU libraries or create descendants until containment exists.
    if sys.stdin.readline().strip() != "start":
        return 2
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    root = args.spec.parent
    result_path = root / "worker_result.json"
    public_runtime_error = ()
    try:
        source = Path(spec["source_root"]).resolve(strict=True)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from mindspine.gait import verify_engine_source
        if not verify_engine_source(source):
            raise RuntimeError("Gait source manifest verification failed")
        sys.path.insert(0, str(source))
        from mindspine.gait_pipeline import analyze_video, GaitRuntimeError
        public_runtime_error = GaitRuntimeError
        from mindspine.gait_reporting import prepare_report_view
        from sconet_video.runtime_paths import APP_VERSION

        if APP_VERSION != "2.0.1":
            raise RuntimeError("Configured gait engine is not V2.0.1")

        def progress(event: dict) -> None:
            atomic_json(root / "progress.json", {
                "stage": str(event.get("stage", "running"))[:80],
                "message": str(event.get("message", "正在处理视频"))[:500],
                "progress": event.get("progress", 0),
                "runtime": event.get("runtime", {}),
            })

        output = Path(spec["output_dir"])
        report = analyze_video(Path(spec["input_path"]), output,
                               source / "models", progress_callback=progress,
                               options=spec["options"], cache_dir=spec.get("cache_dir"))
        if not isinstance(report, dict):
            raise ValueError("The gait engine returned an invalid report")
        # V2.0.1's interpretation/artifact packaging is part of successful work.
        # Preserve the original pipeline report bytes for its provenance hash.
        view = prepare_report_view({"id": spec["id"], "_output": output,
                                    "filename": spec["filename"],
                                    "research_demo": bool(spec["options"].get("research_demo"))}, {})
        status = "rejected" if view.get("status") in {"rejected", "quality_rejected", "invalid"} else "completed"
        message = (view.get("summary") if view.get("requires_target_selection") or view.get("multi_subject") else
                   "视频质量检查未通过，请查看原因" if status == "rejected" else "分析完成，请核对被测者后归档")
        atomic_json(result_path, {"status": status, "report_view_version": view.get("report_view_version"),
                                  "message": message})
        return 0
    except Exception as error:
        # No raw filenames, installation paths, traceback or clinical data in logs.
        # Only the application-owned exception may supply a public diagnostic;
        # arbitrary dependency errors can contain patient paths or filenames.
        known = isinstance(error, public_runtime_error)
        atomic_json(result_path, {"status": "failed", "error_type": type(error).__name__,
                                  "message": error.public_message if known else "步态分析未完成，请检查视频质量与本机推理环境后重试"})
        if known:
            atomic_json(root / "progress.json", {"stage": "error", "progress": 0,
                        "message": error.public_message, "runtime": error.runtime})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
