from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "workspace.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.home = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.auth_js = (ROOT / "web" / "auth.js").read_text(encoding="utf-8")
        cls.personal_html = (ROOT / "web" / "personal-workspace.html").read_text(encoding="utf-8")
        cls.personal_js = (ROOT / "web" / "personal-app.js").read_text(encoding="utf-8")
        cls.personal_case_html = (ROOT / "web" / "personal-case.html").read_text(encoding="utf-8")
        cls.personal_case_js = (ROOT / "web" / "personal-case.js").read_text(encoding="utf-8")
        cls.health_home_html = (ROOT / "web" / "health-home.html").read_text(encoding="utf-8")
        cls.health_home_js = (ROOT / "web" / "health-home.js").read_text(encoding="utf-8")
        cls.health_agent_html = (ROOT / "web" / "health-agent.html").read_text(encoding="utf-8")
        cls.health_agent_js = (ROOT / "web" / "health-agent.js").read_text(encoding="utf-8")
        cls.server = (ROOT / "main.py").read_text(encoding="utf-8")

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

    def test_workspace_requires_account_check_and_home_has_auth_actions(self):
        self.assertIn('MindSpineAuth?.requireUser?.()', self.js)
        self.assertIn('href="login.html">登录</a>', self.home)
        self.assertIn('href="register.html">注册</a>', self.home)
        self.assertIn('id="account-avatar-button"', self.home)
        self.assertIn('/api/auth/login', self.auth_js)
        self.assertIn('/api/auth/register', self.auth_js)
        self.assertIn('id="member-hero-actions"', self.home)
        self.assertIn('memberEntryActions.forEach((element) => { element.hidden = false; });', self.auth_js)

    def test_personal_workspace_has_the_required_single_image_and_video_flow(self):
        self.assertIn('href="personal-workspace.html">个人影像检测', self.home)
        for tab in ("RGB检测", "DR检测", "步态评估"):
            self.assertIn(tab, self.personal_html)
        for endpoint in ("/api/upload", "/api/rgb", "/api/crop", "/api/pseudoct/single", "/api/pseudoct/loop_to_xray", "/api/xray"):
            self.assertIn(endpoint, self.personal_js)
        self.assertIn('MindSpineAuth?.requireUser?.()', self.personal_js)
        self.assertIn('gait-workspace.html', self.personal_js)
        self.assertNotIn("侧弯初筛", self.personal_html)
        self.assertNotIn("模型准备中", self.personal_html)
        self.assertNotIn("一键分析流程", self.personal_html)
        self.assertNotIn("updateVideoScreening", self.personal_js)
        self.assertIn("saveCaseEntry", self.personal_js)
        self.assertIn("/api/personal-case/entries", self.personal_js)

    def test_personal_case_has_profile_trends_and_manual_follow_up(self):
        self.assertIn('href="health-home.html"', self.home)
        self.assertIn('href="personal-case.html"', self.personal_html)
        for element_id in ("case-profile-form", "case-clinical-form", "case-checkin-form", "case-trend-svg", "case-entry-list"):
            self.assertIn(f'id="{element_id}"', self.personal_case_html)
        for endpoint in ("/api/personal-case", "/api/personal-case/profile", "/api/personal-case/entries"):
            self.assertIn(endpoint, self.personal_case_js)
        self.assertIn("MindSpineAuth?.requireUser?.()", self.personal_case_js)
        self.assertIn("window.print()", self.personal_case_js)

    def test_health_center_separates_today_archive_and_controlled_agent(self):
        for page in ("health-home.html", "personal-workspace.html", "personal-case.html", "health-agent.html"):
            self.assertIn(f'href="{page}"', self.health_home_html)
        self.assertIn("#spine-agent-workbench{display:block}", self.personal_case_html)
        self.assertIn('id="case-questionnaire-analysis"', self.personal_case_html)
        for element_id in (
            "agent-triage-status",
            "btn-agent-toggle-consent",
            "agent-chat-form",
            "agent-action-list",
            "agent-questionnaire-analysis",
        ):
            self.assertIn(f'id="{element_id}"', self.health_agent_html)
        for endpoint in (
            "/api/cases/",
            "/agent/messages",
            "/api/agent-actions/",
            "/consent",
        ):
            self.assertIn(endpoint, self.health_agent_js)
        self.assertIn("personal-case.html?modal=questionnaire", self.health_home_html)
        self.assertIn("legacy_timeline", self.health_home_js)
        self.assertIn("Multimodal evidence matrix", self.health_home_html)
        self.assertIn("Case briefing / today", self.health_home_html)
        self.assertIn("agent-sidebar right", self.health_agent_html)
        self.assertIn("当前病例", self.health_agent_html)
        for endpoint in (
            '@app.get("/api/cases/default/workspace")',
            '@app.post("/api/cases/{case_id}/questionnaire")',
            '@app.post("/api/cases/{case_id}/agent/messages")',
            '@app.post("/api/agent-actions/{action_id}/confirm")',
        ):
            self.assertIn(endpoint, self.server)

    def test_personal_workspace_is_server_protected(self):
        self.assertIn('"/personal-workspace.html"', self.server)
        self.assertIn('"/personal-case.html"', self.server)
        self.assertIn('"/health-home.html"', self.server)
        self.assertIn('"/health-agent.html"', self.server)
        self.assertIn('@app.get("/api/personal-case")', self.server)
        self.assertIn('@app.put("/api/personal-case/profile")', self.server)
        self.assertIn('return RedirectResponse(url="/login.html", status_code=303)', self.server)


if __name__ == "__main__":
    unittest.main()
