from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ai_report_service import DeepSeekReportService


class DeepSeekReportServiceTests(unittest.TestCase):
    def test_legacy_model_setting_is_migrated_without_editing_operator_files(self):
        with patch.dict(os.environ, {"DEEPSEEK_MODEL": "deepseek-chat"}, clear=False):
            service = DeepSeekReportService(base_dir="__nonexistent_settings_root__")

        self.assertEqual(service.model, "deepseek-v4-pro")
        self.assertEqual(service.model_migrated_from, "deepseek-chat")

    def test_flash_setting_is_upgraded_to_current_strongest_default(self):
        with patch.dict(os.environ, {"DEEPSEEK_MODEL": "deepseek-v4-flash"}, clear=False):
            service = DeepSeekReportService(base_dir="__nonexistent_settings_root__")

        self.assertEqual(service.model, "deepseek-v4-pro")
        self.assertEqual(service.model_migrated_from, "deepseek-v4-flash")

    def test_no_api_key_keeps_safe_local_copy(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "DEEPSEEK_MODEL": "deepseek-v4-pro"}, clear=False):
            service = DeepSeekReportService(base_dir="__nonexistent_settings_root__")
            service.api_key = ""
            result = service.generate_agent_copy({"case_scope": "adult_spine_followup"}, "先完成成人问卷。")

        self.assertFalse(result["used"])
        self.assertEqual(result["message"], "先完成成人问卷。")


if __name__ == "__main__":
    unittest.main()
