from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindspine.gait import GaitError, GaitJobStore, GaitService, validate_options
from mindspine.gait_api import create_router


SYNTHETIC_WORKER = r'''
import argparse, json, os, subprocess, sys, time, zipfile
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--spec', type=Path)
spec_path = parser.parse_args().spec
if sys.stdin.readline().strip() != 'start':
    raise SystemExit(2)
spec = json.loads(spec_path.read_text(encoding='utf-8'))
root, output = spec_path.parent, Path(spec['output_dir'])
if spec['options']['target_track_id'] == 998:
    (root / 'worker_result.json').write_text(json.dumps({'status':'failed','error_type':'GPUUnavailableError',
        'message':'GPU preflight failed; no slow CPU fallback was started.'}))
    raise SystemExit(1)
if spec['options']['target_track_id'] == 999:
    script = "import time; from pathlib import Path; p=Path(" + repr(str(root / 'child_alive')) + ");\nwhile True:\n p.write_text(str(time.time())); time.sleep(.03)"
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    child = subprocess.Popen([sys.executable, '-c', script], creationflags=flags)
    (root / 'child_pid').write_text(str(child.pid))
    while True: time.sleep(.1)
report = {'report_view_version':'2.0.1', 'status':'completed', 'prediction':None,
          'quality':{'passed':True}, 'analysis':{'summary':'Synthetic test'},
          'artifacts':{'report':'report_v2.json','printable':'report.html','bundle':'report_v2.zip'}}
if spec['options'].get('analysis_mode') == 'all':
    report.update(multi_subject=True, subject_results=[{'track_id':1}, {'track_id':2}])
(output / 'report_v2.json').write_text(json.dumps(report), encoding='utf-8')
(output / 'report.html').write_text('<html>Synthetic test</html>', encoding='utf-8')
with zipfile.ZipFile(output / 'report_v2.zip', 'w') as z:
    z.write(output / 'report.html','report.html')
(root / 'worker_result.json').write_text(json.dumps({'status':'completed','message':'Synthetic complete'}))
'''


class SyntheticService(GaitService):
    def health(self):
        result = super().health()
        result["available"] = not self._stop.is_set()
        return result


class GaitServiceTests(unittest.TestCase):
    def setUp(self):
        # Python 3.13's Windows mkdtemp ACL can exclude a managed sandbox token.
        # Keep synthetic data inside the explicitly writable project runtime dir.
        self.test_root = Path(__file__).resolve().parents[1] / "temp_uploads"
        self.root = self.test_root / ("gait-test-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.worker = self.root / "synthetic_worker.py"
        self.worker.write_text(SYNTHETIC_WORKER, encoding="utf-8")
        self.services = []

    def tearDown(self):
        for service in reversed(self.services):
            service.close()
        resolved = self.root.resolve()
        self.assertEqual(resolved.parent, self.test_root.resolve())
        self.assertTrue(resolved.name.startswith("gait-test-"))
        shutil.rmtree(resolved)

    def service(self, folder="data", **kwargs):
        instance = SyntheticService(self.root / folder, source_root=self.root,
                                    python_executable=sys.executable, worker_script=self.worker, **kwargs)
        self.services.append(instance)
        return instance

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.04)
        self.fail("Timed out waiting for synthetic task")

    def completed(self, service, job):
        def done():
            current = service.get("owner", job["id"])
            if current["status"] in {"failed", "cancelled"}:
                self.fail(str(current))
            return current if current["status"] == "completed" else None
        return self.wait_for(done)

    def test_atomic_admission_caps_ten_simultaneous_requests(self):
        store = GaitJobStore(self.root / "admission.sqlite3")
        def reserve(index):
            try:
                return store.reserve("owner" + str(index), "case", "test.mp4", ".mp4", {}, 10, 2, None)["id"]
            except GaitError as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(reserve, range(12)))
        self.assertEqual(sum(isinstance(result, str) for result in results), 10)
        self.assertEqual(results.count(429), 2)

    def test_options_reject_invalid_temporal_and_tracking_input(self):
        for options in ({"start_seconds": float("nan")}, {"end_seconds": 60}, {"start_seconds": 10 ** 500},
                        {"start_seconds": 10, "end_seconds": 5}, {"target_track_id": True},
                        {"research_demo": "false"}, {"arbitrary_path": "x"},
                        {"allow_cpu_fallback": "false"}, {"allow_cpu_fallback": 1},
                        {"analysis_mode": "batch"}, {"analysis_mode": []}, {"device": []},
                        {"analysis_mode": "all", "target_track_id": 2}, {"cache_dir": "x"}):
            with self.subTest(options=options), self.assertRaises(GaitError):
                validate_options(options)
        self.assertEqual(validate_options({"device": "cpu"})["device"], "cpu")
        self.assertEqual(validate_options({})["analysis_mode"], "single")
        self.assertFalse(validate_options({})["allow_cpu_fallback"])
        self.assertTrue(validate_options({"allow_cpu_fallback": True})["allow_cpu_fallback"])
        self.assertEqual(validate_options({"analysis_mode": "all"})["analysis_mode"], "all")

    def test_rerun_resets_target_and_supplies_only_same_case_parent_cache(self):
        service = self.service()
        original = service.create("owner", "case_A", io.BytesIO(b"synthetic"), "video.mp4", {"target_track_id": 2})
        self.completed(service, original)
        retry = service.rerun("owner", original["id"], {"target_track_id": None, "analysis_mode": "all"})
        self.completed(service, retry)
        self.assertIsNone(retry["options"]["target_track_id"])
        retry_root = service._paths(service.store.get(retry["id"]))[0]
        spec = json.loads((retry_root / "worker_spec.json").read_text(encoding="utf-8"))
        expected = service._paths(service.store.get(original["id"]))[2]
        self.assertEqual(Path(spec["cache_dir"]), expected)
        with self.assertRaises(GaitError):
            service.create("owner", "case_B", io.BytesIO(b"synthetic"), "video.mp4", parent_job_id=original["id"])
        with self.assertRaises(GaitError):
            service.create("other", "case_A", io.BytesIO(b"synthetic"), "video.mp4", parent_job_id=original["id"])
        with self.assertRaises(GaitError):
            service.rerun("owner", original["id"], {"cache_dir": str(expected)})
        # Recheck authorization when launching: a corrupted DB linkage must not
        # permit another owner's cache even after the initial request gate.
        job = service.store.get(retry["id"])
        self.assertIsNone(service._parent_cache({**job, "user_id": "other"}))
        self.assertIsNone(service._parent_cache({**job, "case_id": "case_B"}))
        self.assertIsNone(service._parent_cache({**job, "parent_job_id": "missing"}))
        outside = self.root / "different_case_cache"
        outside.mkdir()
        with patch.object(service, "_paths", return_value=(outside, outside / "input.mp4", outside)):
            self.assertIsNone(service._parent_cache(job))

    def test_multiple_subjects_cannot_be_archived_as_one_person(self):
        service = self.service()
        job = service.create("owner", "case_A", io.BytesIO(b"synthetic"), "video.mp4", {"analysis_mode": "all"})
        self.completed(service, job)
        with self.assertRaises(GaitError) as blocked:
            service.review_archive("owner", job["id"], "Reviewed", True)
        self.assertEqual(blocked.exception.status_code, 409)
        self.assertIsNone(service.selected_result("owner", "case_A"))
        service.review_archive("owner", job["id"], "Can still add a review without archiving")
        selected = service.rerun("owner", job["id"], {"analysis_mode": "single", "target_track_id": 2})
        self.completed(service, selected)
        service.review_archive("owner", selected["id"], "Actual patient selected", True)
        self.assertEqual(service.selected_result("owner", "case_A")["job_id"], selected["id"])
        # Guard the saved report as well as options (old/imported job metadata).
        report_path = service._paths(service.store.get(selected["id"]))[2] / "report_v2.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["multi_subject"] = True
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaises(GaitError):
            service.review_archive("owner", selected["id"], "Incorrect multi report", True)

    def test_worker_actionable_failure_is_not_overwritten_by_generic_message(self):
        service = self.service()
        job = service.create("owner", "case_A", io.BytesIO(b"synthetic"), "video.mp4", {"target_track_id": 998})
        result = self.wait_for(lambda: (value if (value := service.get("owner", job["id"]))["status"] == "failed" else None))
        self.assertEqual(result["error_type"], "GPUUnavailableError")
        self.assertIn("no slow CPU fallback", result["message"])

    def test_runtime_exposes_only_safe_bounded_fields(self):
        service = self.service()
        job = service.create("owner", "case_A", io.BytesIO(b"synthetic"), "video.mp4")
        self.completed(service, job)
        path = service._paths(service.store.get(job["id"]))[0] / "progress.json"
        path.write_text(json.dumps({"runtime": {
            "device_requested": "auto", "device_effective": "cuda", "gpu_name": "NVIDIA GeForce RTX 4050",
            "actual_device": "cuda", "requested_device": "auto", "tracking_cache_reused": True,
            "stage_timings_seconds": {"tracking": 8.2, "classification": .2, "private/path": 1},
            "cpu_fallback": False, "cache_used": True, "elapsed_seconds": 9.5,
            "processed_frames": 118, "total_frames": -1, "cache_dir": "private-path", "token": "private-value"
        }}), encoding="utf-8")
        runtime = service.get("owner", job["id"])["runtime"]
        self.assertEqual(runtime["device_effective"], "cuda")
        self.assertEqual(runtime["actual_device"], "cuda")
        self.assertEqual(runtime["stage_timings_seconds"], {"tracking": 8.2, "classification": .2})
        self.assertEqual(runtime["processed_frames"], 118)
        self.assertNotIn("total_frames", runtime)
        self.assertNotIn("cache_dir", runtime)
        self.assertNotIn("token", runtime)
        path.write_text('{"runtime": {"gpu_name": "C:/private/file", "elapsed_seconds": true}}', encoding="utf-8")
        self.assertEqual(service.get("owner", job["id"])["runtime"], {})
        path.write_text('[]', encoding="utf-8")
        self.assertEqual(service.get("owner", job["id"])["runtime"], {})

    def test_report_owner_allowlist_review_and_rerun(self):
        service = self.service()
        job = service.create("owner", "case_A", io.BytesIO(b"synthetic video"), "video.mp4")
        self.completed(service, job)
        with self.assertRaises(GaitError) as denied:
            service.report("other", job["id"])
        self.assertEqual(denied.exception.status_code, 404)
        with self.assertRaises(GaitError):
            service.artifact("owner", job["id"], "../worker_spec.json")
        self.assertTrue(service.artifact("owner", job["id"], "report.html").is_file())
        with self.assertRaises(GaitError):
            service.review_archive("owner", job["id"], "", True)
        service.review_archive("owner", job["id"], "已确认原片与人物，仅作研究记录。", True)
        self.assertEqual(service.selected_result("owner", "case_A")["job_id"], job["id"])
        self.assertIsNone(service.selected_result("owner", "case_B"))
        retry = service.rerun("owner", job["id"], {"target_track_id": 2})
        self.completed(service, retry)
        self.assertEqual(retry["parent_job_id"], job["id"])
        self.assertEqual(retry["options"]["target_track_id"], 2)
        self.assertFalse(retry["review"]["archived"])
        service.review_archive("owner", retry["id"], "选用更新的结果。", True)
        service.review_archive("owner", job["id"], "仅补充旧记录备注。")
        self.assertEqual(service.selected_result("owner", "case_A")["job_id"], retry["id"])
        self.assertTrue(service.get("owner", job["id"])["review"]["archived"])

    def test_agent_confirmation_key_prevents_duplicate_reruns(self):
        service = self.service()
        original = service.create("owner", "case", io.BytesIO(b"synthetic"), "video.mp4")
        self.completed(service, original)
        def rerun(_):
            return service.rerun("owner", original["id"], {}, idempotency_key="agent_action_1")
        with ThreadPoolExecutor(max_workers=4) as executor:
            replies = list(executor.map(rerun, range(4)))
        self.assertEqual(len({reply["id"] for reply in replies}), 1)
        self.completed(service, replies[0])
        self.assertEqual(len(service.list("owner", "case")), 2)

    def test_cancel_really_stops_worker_descendant(self):
        service = self.service()
        job = service.create("owner", "case", io.BytesIO(b"synthetic"), "video.mp4", {"target_track_id": 999})
        root = service.root / "owner" / "case" / job["id"]
        self.wait_for(lambda: (root / "child_alive").exists())
        result = service.cancel("owner", job["id"])
        self.assertEqual(result["status"], "cancelled")
        self.wait_for(lambda: job["id"] not in service._processes)
        value = (root / "child_alive").read_text()
        time.sleep(.25)
        self.assertEqual(value, (root / "child_alive").read_text())

    def test_timeout_and_shutdown_cancel_inflight_jobs(self):
        service = self.service(timeout_seconds=.7)
        job = service.create("owner", "case", io.BytesIO(b"synthetic"), "video.mp4", {"target_track_id": 999})
        current = self.wait_for(lambda: (value if (value := service.get("owner", job["id"]))["status"] == "failed" else None))
        self.assertEqual(current["error_type"], "Timeout")
        job2 = service.create("owner", "case", io.BytesIO(b"synthetic"), "video.mp4", {"target_track_id": 999})
        self.wait_for(lambda: job2["id"] in service._processes)
        queued = service.create("owner", "case", io.BytesIO(b"synthetic"), "second.mp4")
        service.close()
        self.assertEqual(service.get("owner", job2["id"])["status"], "cancelled")
        self.assertEqual(service.get("owner", queued["id"])["status"], "cancelled")

    def test_logout_cancels_only_owners_unfinished_jobs(self):
        service = self.service()
        done = service.create("owner", "case", io.BytesIO(b"synthetic"), "done.mp4")
        self.completed(service, done)
        running = service.create("owner", "case", io.BytesIO(b"synthetic"), "running.mp4", {"target_track_id": 999})
        self.wait_for(lambda: running["id"] in service._processes)
        queued = service.create("owner", "case", io.BytesIO(b"synthetic"), "queued.mp4")
        other = service.create("other", "case2", io.BytesIO(b"synthetic"), "other.mp4")
        cancelled = service.cancel_user("owner")
        self.assertEqual({job["id"] for job in cancelled}, {running["id"], queued["id"]})
        self.assertEqual(service.get("owner", done["id"])["status"], "completed")
        self.assertNotEqual(service.get("other", other["id"])["status"], "cancelled")

    def test_restart_marks_unfinished_metadata_interrupted(self):
        path = self.root / "restart" / "gait" / "jobs.sqlite3"
        store = GaitJobStore(path)
        job = store.reserve("owner", "case", "video.mp4", ".mp4", validate_options({}), 10, 2, None)
        service = self.service(folder="restart")
        self.assertEqual(service.get("owner", job["id"])["stage"], "interrupted")
        with self.assertRaises(RuntimeError):
            self.service(folder="restart")

    def test_http_case_permission_and_video_range_delivery(self):
        service = self.service()
        app = FastAPI()
        def authorize(owner, case_id):
            if case_id != "owned_case":
                raise PermissionError("Denied")
        app.include_router(create_router(service, lambda request: request.headers.get("x-user"), authorize))
        with TestClient(app) as client:
            denied = client.post("/api/gait/jobs", headers={"x-user": "owner"}, data={"case_id": "other_case"}, files={"file": ("video.mp4", b"synthetic")})
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(service.list("owner"), [])
            accepted = client.post("/api/gait/jobs", headers={"x-user": "owner"}, data={"case_id": "owned_case"}, files={"file": ("video.mp4", b"synthetic")})
            self.assertEqual(accepted.status_code, 202)
            job = accepted.json()["job"]
            self.completed(service, job)
            rejected_options = client.post(f"/api/gait/jobs/{job['id']}/rerun", headers={"x-user": "owner"},
                                           json={"options": {}, "cache_dir": "untrusted"})
            self.assertEqual(rejected_options.status_code, 400)
            denied = client.get(f"/api/gait/jobs/{job['id']}/report", headers={"x-user": "other"})
            self.assertEqual(denied.status_code, 404)
            # Range implementation is exercised on a declared synthetic artifact.
            response = client.get(f"/api/gait/jobs/{job['id']}/files/report.html", headers={"x-user": "owner", "Range": "bytes=0-3"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b"<htm")
            self.assertIn("no-store", response.headers["cache-control"])


if __name__ == "__main__":
    unittest.main()
