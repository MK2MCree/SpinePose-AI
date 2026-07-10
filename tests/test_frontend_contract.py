from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "workspace.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_batch_result_ids_match_javascript(self):
        for element_id in ("xray-batch-result-list", "rgb-batch-result-list"):
            self.assertIn(f'id="{element_id}"', self.html)
            self.assertIn(f'this.el("{element_id}")', self.js)

    def test_visible_batch_buttons_are_bound(self):
        for element_id in ("btn-run-xray-batch-left", "btn-hide-batch-progress"):
            self.assertIn(f'id="{element_id}"', self.html)
            self.assertIn(f'this.bindClick("{element_id}"', self.js)

    def test_case_id_is_sent_for_stateful_actions(self):
        self.assertIn('JSON.stringify({ id: this.currentImageId, wl, ww })', self.js)
        self.assertIn('JSON.stringify({ id: this.currentImageId })', self.js)
        self.assertIn('params.set("id", this.currentImageId)', self.js)


if __name__ == "__main__":
    unittest.main()

