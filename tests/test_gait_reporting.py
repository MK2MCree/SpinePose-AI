from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import types
import unittest
import uuid
from unittest.mock import patch
import zipfile

from mindspine.gait_pipeline import render_report_html, GaitInputError, GaitRuntimeError
from mindspine.gait_reporting import prepare_report_view


class GaitReportingTests(unittest.TestCase):
    def report(self):
        return {"multi_subject": True, "subject_results": [
            {"track_id": 2, "status": "completed", "research_demo": True,
             "raw_model_prediction": {"probabilities": {"negative": .7, "neutral": .2, "positive": .1}},
             "quality": {"passed": True, "issues": [{"message": "<script>unsafe()</script>"}]}}],
            "quality": {"passed": True}, "prediction": None, "job_id": "test-job",
            "model": {"name": "verified-sconet", "checkpoint_sha256": "a" * 64},
            "runtime": {"actual_device": "cuda", "adapter_version": "mindspine-gait-v2"},
            "original_report_sha256": "b" * 64, "report_view_version": "2.0.1"}

    def test_multi_html_has_separate_results_audit_and_research_scope(self):
        html = render_report_html(self.report(), lambda report: "WRONG SINGLE SUBJECT SUMMARY")
        for text in ("人物 ID 2", "阴性类", "研究演示模式", "verified-sconet", "a" * 64, "b" * 64, "test-job", "2.0.1"):
            self.assertIn(text, html)
        self.assertNotIn("WRONG SINGLE SUBJECT SUMMARY", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_selection_html_never_claims_classification(self):
        html = render_report_html({"requires_target_selection": True, "quality": {"passed": False}}, lambda report: "wrong")
        self.assertIn("请选择被测者", html)
        self.assertNotIn("已生成模型分数", html)

    def test_safe_input_errors_remain_actionable_and_value_errors(self):
        error = GaitInputError("请选择有效的视频片段")
        self.assertIsInstance(error, GaitRuntimeError)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(error.public_message, "请选择有效的视频片段")

    def test_versioned_json_html_zip_are_consistent(self):
        parent = Path(__file__).resolve().parents[1] / "temp_uploads"
        root = parent / ("gait-report-test-" + uuid.uuid4().hex)
        root.mkdir(parents=True)
        try:
            original = self.report()
            original.pop("original_report_sha256")
            original["artifacts"] = {"report": "report_v2.json", "printable": "report.html", "bundle": "report_v2.zip", "original_report": "report.json"}
            original_bytes = json.dumps(original).encode()
            (root / "report.json").write_bytes(original_bytes)
            def fake_prepare(job, cache):
                report = json.loads((root / "report.json").read_text())
                report["summary"] = "wrong single-subject summary"
                report["analysis"] = {"mode": "unavailable"}
                report["original_report_sha256"] = hashlib.sha256(original_bytes).hexdigest()
                return report
            reporting = types.ModuleType("sconet_video.reporting")
            reporting.prepare_report_view = fake_prepare
            reporting.SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
            interpretation = types.ModuleType("sconet_video.interpretation")
            interpretation.render_report_html = lambda report: "wrong"
            cache = {}
            with patch.dict(sys.modules, {"sconet_video.reporting": reporting, "sconet_video.interpretation": interpretation}):
                report = prepare_report_view({"id": "test-job", "_output": root}, cache)
            self.assertEqual(report["analysis"]["mode"], "multi_subject")
            self.assertEqual((root / "report.json").read_bytes(), original_bytes)
            with zipfile.ZipFile(root / "report_v2.zip") as bundle:
                self.assertEqual(json.loads(bundle.read("report_v2.json")), report)
                self.assertEqual(bundle.read("report.html"), (root / "report.html").read_bytes())
                self.assertEqual(bundle.read("report.json"), original_bytes)
            self.assertEqual(cache["test-job"][1], report)
        finally:
            self.assertEqual(root.resolve().parent, parent.resolve())
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
