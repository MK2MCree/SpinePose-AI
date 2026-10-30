from contextlib import nullcontext
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from mindspine import gait_pipeline as adapter


class DeviceTests(unittest.TestCase):
    def torch(self):
        value = Mock()
        value.__version__ = "test"
        value.version.cuda = "test"
        value.cuda.is_available.return_value = True
        value.cuda.get_device_name.return_value = "Synthetic GPU"
        value.inference_mode.side_effect = nullcontext
        return value

    def test_gpu_kernels_run_even_if_display_name_fails(self):
        torch = self.torch()
        torch.cuda.get_device_name.side_effect = AssertionError("PRIVATE")
        device, metadata = adapter.resolve_device(torch_module=torch, nms=Mock())
        self.assertEqual(device, "cuda")
        self.assertEqual(metadata["device_name_warning"], "AssertionError")
        self.assertNotIn("PRIVATE", json.dumps(metadata))
        torch.cuda.init.assert_called_once()
        torch.cuda.synchronize.assert_called_once()

    def test_one_retry_and_no_silent_cpu(self):
        torch = self.torch()
        torch.cuda.init.side_effect = AssertionError("PRIVATE PATH")
        with self.assertRaises(adapter.GPUUnavailableError) as failure:
            adapter.resolve_device(torch_module=torch, nms=Mock())
        self.assertEqual(torch.cuda.init.call_count, 2)
        self.assertEqual(failure.exception.runtime["error_code"], "cuda_preflight_failed")
        self.assertNotIn("PRIVATE", str(failure.exception))
        self.assertNotIn("PRIVATE", json.dumps(failure.exception.runtime))

    def test_transient_initialization_retries_once(self):
        torch = self.torch()
        torch.cuda.init.side_effect = [AssertionError(), None]
        device, runtime = adapter.resolve_device(torch_module=torch, nms=Mock())
        self.assertEqual(device, "cuda")
        self.assertEqual(runtime["probe_attempts"], 2)

    def test_cpu_only_after_explicit_opt_in(self):
        torch = self.torch()
        torch.cuda.is_available.return_value = False
        device, runtime = adapter.resolve_device(allow_cpu_fallback=True, torch_module=torch)
        self.assertEqual(device, "cpu")
        self.assertTrue(runtime["cpu_opt_in"])
        with self.assertRaises(adapter.GPUUnavailableError):
            adapter.resolve_device("cuda", True, torch_module=torch)
        self.assertEqual(adapter.resolve_device("cpu")[0], "cpu")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.root = Path(__file__).resolve().parents[1] / "temp_uploads" / ("gait-pipeline-test-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.video = self.root / "synthetic.mp4"
        self.video.write_bytes(b"synthetic video, no person data")
        self.model_dir = self.root / "models"
        self.model_dir.mkdir()
        (self.model_dir / "frontend_manifest.json").write_text('{}')
        self.out = self.root / "output"
        self.info = {"width": 640, "height": 720, "fps": 30., "frame_count": 30, "duration_seconds": 1.}
        self.tracks = [{"track_id": i, "bbox_xyxy": [10. + i * 100, 10., 90. + i * 100, 700.], "score": .9} for i in range(4)]
        self.frontend = Mock()
        self.frontend.metadata.return_value = {"detector_device": "cuda"}
        self.frontend.track.side_effect = lambda *args: self.tracks
        self.classifier = Mock()
        self.classifier.metadata.return_value = {"device": "cuda"}
        self.upstream = ModuleType("sconet_video.pipeline")
        self.upstream.MODEL_SHA256 = "frozen-sha"
        self.upstream.MODEL_NAME = "frozen.pt"
        self.upstream.ANALYSIS_FPS = 15.
        self.upstream.MAX_CLIP_SECONDS = 45.
        self.upstream.GENERATED_FILES = {"tracks.json", "report.json", "report.html", "report.zip"}
        self.upstream.probe_video = Mock(return_value=self.info)
        self.upstream._models = Mock(return_value=(self.frontend, self.classifier))
        self.upstream.sampled_frames = lambda *args: iter([(i * 2, i / 15, np.zeros((720, 640, 3), dtype=np.uint8), False) for i in range(2)])
        self.upstream.summarize_tracks = lambda records, h: [dict(v, rank_score=1., frames=2) for v in self.tracks]
        package = ModuleType("sconet_video")
        package.pipeline = self.upstream
        interpretation = ModuleType("sconet_video.interpretation")
        interpretation.build_analysis = lambda report: {"mode": "unavailable", "summary": "no scores", "model_scores": {}}
        interpretation.render_report_html = lambda report: "<html>upstream</html>"
        self.module_patch = patch.dict(sys.modules, {"sconet_video": package, "sconet_video.pipeline": self.upstream,
                                                   "sconet_video.interpretation": interpretation})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.device_patch = patch.object(adapter, "resolve_device", return_value=("cuda", {"actual_device": "cuda"}))
        self.device_patch.start()
        self.addCleanup(self.device_patch.stop)
        self.preview_patch = patch.object(adapter, "_preview")
        self.preview_patch.start()
        self.addCleanup(self.preview_patch.stop)

    def tearDown(self):
        self.assertEqual(self.root.parent.name, "temp_uploads")
        self.assertTrue(self.root.name.startswith("gait-pipeline-test-"))
        shutil.rmtree(self.root)

    def analyze(self, **kwargs):
        return adapter.analyze_video(self.video, self.out, self.model_dir, **kwargs)

    def subject(self, *args):
        ident, demo = args[10:12]
        passed = ident != 3
        return {"track_id": ident, "status": "completed" if passed else "rejected",
                "prediction": {"probabilities": {"negative": .5, "neutral": .3, "positive": .2}} if passed else None,
                "raw_model_prediction": None, "quality": {"passed": passed, "issues": []},
                "timings_seconds": {"segmentation": .1, "classification": .01}}

    def test_ambiguous_stops_before_segmentation(self):
        with patch.object(adapter, "_subject") as subject:
            report = self.analyze()
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(report["requires_target_selection"])
        self.assertIsNone(report["tracking"]["selected_track_id"])
        subject.assert_not_called()
        self.classifier.predict.assert_not_called()
        self.assertTrue((self.out / "tracking_cache.json").is_file())

    def test_cache_reuse_after_selection_skips_detector(self):
        self.analyze()
        original = self.out
        self.out = self.root / "second"
        self.frontend.detect.reset_mock()
        with patch.object(adapter, "_subject", side_effect=self.subject):
            report = self.analyze(options={"target_track_id": 2}, cache_dir=original)
        self.frontend.detect.assert_not_called()
        self.assertTrue(report["tracking"]["cache_reused"])
        self.assertEqual(report["tracking"]["selected_track_id"], 2)
        self.assertEqual(len(report["subject_results"]), 1)

    def test_cache_tamper_recomputes(self):
        self.analyze()
        original = self.out
        (original / "tracks.json").write_text('[]')
        self.out = self.root / "second"
        self.frontend.detect.reset_mock()
        self.analyze(cache_dir=original)
        self.assertEqual(self.frontend.detect.call_count, 2)

    def test_source_change_invalidates_cache(self):
        self.analyze()
        original = self.out
        self.video.write_bytes(b"changed synthetic video")
        self.out = self.root / "second"
        self.frontend.detect.reset_mock()
        self.analyze(cache_dir=original)
        self.assertEqual(self.frontend.detect.call_count, 2)

    def test_model_manifest_change_invalidates_cache(self):
        self.analyze()
        original = self.out
        (self.model_dir / "frontend_manifest.json").write_text('{"version":2}')
        self.out = self.root / "second"
        self.frontend.detect.reset_mock()
        self.analyze(cache_dir=original)
        self.assertEqual(self.frontend.detect.call_count, 2)

    def test_all_has_four_separate_results_and_no_patient_prediction(self):
        with patch.object(adapter, "_subject", side_effect=self.subject) as subject:
            report = self.analyze(options={"analysis_mode": "all"})
        self.assertEqual(subject.call_count, 4)
        self.assertTrue(report["multi_subject"])
        self.assertIsNone(report["prediction"])
        self.assertIsNone(report["tracking"]["selected_track_id"])
        self.assertEqual([v["track_id"] for v in report["subject_results"]], [0, 1, 2, 3])
        self.assertEqual(report["subject_results"][3]["status"], "rejected")
        self.assertIsNone(report["subject_results"][3]["prediction"])
        self.assertEqual(report["quality"]["subjects_passed"], 3)
        self.assertEqual(report["runtime"]["stage_timings_seconds"]["segmentation"], .4)
        html = (self.out / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("no scores", html)
        self.assertIn("人物 ID 3", html)
        self.assertIn("阴性类", html)

    def test_invalid_all_with_target_rejected(self):
        with self.assertRaises(ValueError):
            self.analyze(options={"analysis_mode": "all", "target_track_id": 2})

    def test_gpu_failure_never_loads_models(self):
        with patch.object(adapter, "resolve_device", side_effect=adapter.GPUUnavailableError("safe")):
            with self.assertRaises(adapter.GPUUnavailableError):
                self.analyze()
        self.upstream._models.assert_not_called()
        self.frontend.detect.assert_not_called()

    def test_invalid_cache_record_coordinates(self):
        records = [{"source_frame": 0, "timestamp": 0., "tracks": self.tracks}]
        self.assertTrue(adapter._validate_records(records, self.info, 0., 1.))
        self.tracks[0]["bbox_xyxy"][0] = float("nan")
        self.assertFalse(adapter._validate_records(records, self.info, 0., 1.))

    def test_quality_error_never_classifies_even_in_demo(self):
        import numpy as np
        self.frontend.segment.return_value = np.zeros((720, 640), dtype=np.uint8)
        self.frontend.last_native_mask = None
        self.frontend.last_segmentation = {}
        self.upstream.align_silhouette_frame = lambda *args: (np.ones((64, 64), dtype=np.uint8), SimpleNamespace(to_dict=lambda: {"accepted_index": 0}))
        self.upstream.quality_summary = lambda *args: {"passed": False, "issues": [{"code": "insufficient_frames", "severity": "error"}]}
        records = [{"source_frame": i * 2, "timestamp": i / 15, "tracks": self.tracks} for i in range(2)]
        result = adapter._subject(self.upstream, self.frontend, self.classifier, self.video, self.out, self.info,
                                  0., 1., records, self.tracks, 0, True, lambda *args: None, 0, 1, False)
        self.assertEqual(result["status"], "rejected")
        self.assertIsNone(result["raw_model_prediction"])
        self.classifier.predict.assert_not_called()


if __name__ == "__main__":
    unittest.main()
