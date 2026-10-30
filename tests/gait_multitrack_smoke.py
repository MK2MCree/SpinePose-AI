"""Opt-in real GPU/worker/API test using the original four-person PUBLIC sample.

Run from the project root with the normal application Python interpreter.
No production accounts or records are opened. Outputs remain in an isolated
validation directory so the reports can be inspected after the test.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import uuid
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindspine.gait import DEFAULT_SOURCE, GaitService, GaitError
from mindspine.gait_api import create_router


def run():
    root = Path(__file__).resolve().parents[1]
    output = root / ".analysis_tmp" / ("gait-validation-" + uuid.uuid4().hex[:10])
    output.mkdir(parents=True)
    sample = DEFAULT_SOURCE / "samples" / "opengait_gallery_original.mp4"
    assert sample.is_file(), "Public regression sample missing"
    service = GaitService(output, timeout_seconds=240)
    app = FastAPI()
    app.include_router(create_router(service, lambda request: "validation-owner",
                                    lambda owner, case: None if case == "public-sample" else (_ for _ in ()).throw(PermissionError())))
    checks = []
    results = {}
    try:
        with TestClient(app) as client:
            def await_job(job):
                started = time.monotonic()
                previous = None
                while job["status"] in {"uploading", "queued", "running"}:
                    marker = job["stage"]
                    if marker != previous:
                        print(json.dumps({"stage": marker, "elapsed_seconds": round(time.monotonic() - started, 1),
                                          "runtime": job.get("runtime", {})}, ensure_ascii=False), flush=True)
                        previous = marker
                    assert time.monotonic() - started < 260, "Bounded inference deadline exceeded"
                    time.sleep(.5)
                    response = client.get("/api/gait/jobs/" + job["id"])
                    response.raise_for_status()
                    job = response.json()["job"]
                assert job["status"] in {"completed", "rejected"}, job
                response = client.get(f"/api/gait/jobs/{job['id']}/report")
                response.raise_for_status()
                report = response.json()["report"]
                assert report["runtime"]["actual_device"] == "cuda", "CPU is not accepted as GPU validation"
                return job, report

            with sample.open("rb") as source:
                response = client.post("/api/gait/jobs", data={"case_id": "public-sample",
                                       "options_json": json.dumps({"device": "auto"})},
                                       files={"file": ("public-four-person.mp4", source, "video/mp4")})
            assert response.status_code == 202, response.text
            initial, report = await_job(response.json()["job"])
            assert report["requires_target_selection"] is True
            assert report["prediction"] is None and not report["subject_results"]
            assert len(report["tracking"]["tracks"]) == 4
            assert report["quality"]["sampled_frames"] == 118
            results["automatic"] = {"elapsed_seconds": report["elapsed_seconds"], "runtime": report["runtime"]}
            checks.append("GPU detection stops for early target selection")
            preview = client.get(f"/api/gait/jobs/{initial['id']}/files/overlay.mp4", headers={"Range": "bytes=0-127"})
            assert preview.status_code == 206
            response = client.post(f"/api/gait/jobs/{initial['id']}/rerun", json={"options": {"analysis_mode": "all", "target_track_id": None}})
            assert response.status_code == 202, response.text
            multi, report = await_job(response.json()["job"])
            assert report["multi_subject"] is True and report["prediction"] is None
            assert report["tracking"]["cache_reused"] is True
            assert {v["track_id"] for v in report["subject_results"]} == {1, 2, 3, 4}
            assert all(v["status"] == "completed" and v["prediction"] is not None for v in report["subject_results"])
            assert report["analysis"]["mode"] == "multi_subject"
            html = client.get(f"/api/gait/jobs/{multi['id']}/files/report.html").text
            assert "暂无完整分类结果" not in html
            for ident in (1, 2, 3, 4):
                assert f"ID {ident}" in html
            refused = client.post(f"/api/gait/jobs/{multi['id']}/review", json={"notes": "public validation", "archive": True})
            assert refused.status_code in {400, 409, 422}
            bundle = service.artifact("validation-owner", multi["id"], "report_v2.zip")
            with zipfile.ZipFile(bundle) as archive:
                saved = json.loads(archive.read("report_v2.json"))
                assert len(saved["subject_results"]) == 4 and saved["analysis"]["mode"] == "multi_subject"
            results["all_tracks"] = {"elapsed_seconds": report["elapsed_seconds"], "runtime": report["runtime"],
                                      "subjects": [{"track_id": v["track_id"], "status": v["status"],
                                                    "scores": v["prediction"]["probabilities"]} for v in report["subject_results"]]}
            checks.append("four independent real classifications, cached detection, matching JSON/HTML/ZIP, multi-person archive refused")
            response = client.post(f"/api/gait/jobs/{multi['id']}/rerun", json={"options": {"analysis_mode": "single", "target_track_id": 2}})
            assert response.status_code == 202, response.text
            single, report = await_job(response.json()["job"])
            assert report["tracking"]["cache_reused"] is True
            assert report["prediction"] is not None and report["quality"]["passed"]
            assert report["tracking"]["selected_track_id"] == 2 and not report["multi_subject"]
            response = client.post(f"/api/gait/jobs/{single['id']}/review", json={"notes": "公开样例，仅验证归档隔离，不是患者诊断。", "archive": True})
            assert response.status_code == 200, response.text
            results["single_track"] = {"elapsed_seconds": report["elapsed_seconds"], "runtime": report["runtime"]}
            checks.append("single-person cached analysis and explicit archive")
            try:
                service.report("unrelated-owner", multi["id"])
                raise AssertionError("Cross-owner report exposed")
            except GaitError as error:
                assert error.status_code == 404
        summary = {"checks": checks, "runs": results, "public_sample_only": True,
                   "output_directory": str(output), "multi_report": str(bundle.parent / "report.html")}
        (output / "validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    finally:
        service.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()
