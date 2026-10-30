from __future__ import annotations

import unittest

from mindspine.spine_agent import (
    SpineAgentPermissionError,
    SpineAgentStore,
    SpineHealthAgent,
    capture_quality,
    chunk_knowledge_sections,
    measurements_comparable,
)


OWNER = "a" * 32
COLLABORATOR = "b" * 32


class SpineAgentStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = SpineAgentStore(":memory:")
        self.case = self.store.ensure_default_case(OWNER)

    def test_case_is_private_until_owner_invites_a_member(self):
        with self.assertRaises(SpineAgentPermissionError):
            self.store.get_case(COLLABORATOR, self.case["id"])

        membership = self.store.invite_member(OWNER, self.case["id"], COLLABORATOR, "clinician", None)

        self.assertEqual(membership["role"], "clinician")
        self.assertEqual(self.store.get_case(COLLABORATOR, self.case["id"])["role"], "clinician")

    def test_urgent_rule_wins_and_blocks_automatic_rehab(self):
        questionnaire = self.store.submit_questionnaire(
            OWNER,
            self.case["id"],
            {
                "adult_confirmed": True,
                "pain_score": 1,
                "activity_limit": 0,
                "red_flags": ["progressive_weakness"],
            },
        )
        response = SpineHealthAgent(self.store).respond(OWNER, self.case["id"], "请给我制定康复训练计划")

        self.assertEqual(questionnaire["triage"]["risk_level"], "urgent")
        self.assertFalse(questionnaire["triage"]["automation_allowed"])
        self.assertEqual(response["safety_status"], "urgent")
        self.assertEqual(response["proposed_actions"][0]["kind"], "request_clinical_review")

    def test_missing_required_scores_are_incomplete_and_block_automation(self):
        questionnaire = self.store.submit_questionnaire(
            OWNER,
            self.case["id"],
            {"adult_confirmed": True, "red_flags": []},
        )
        response = SpineHealthAgent(self.store).respond(OWNER, self.case["id"], "请给我制定康复训练计划")

        self.assertEqual(questionnaire["triage"]["risk_level"], "incomplete")
        self.assertFalse(questionnaire["triage"]["data_complete"])
        self.assertFalse(questionnaire["triage"]["automation_allowed"])
        self.assertEqual(response["safety_status"], "incomplete")
        self.assertEqual(response["proposed_actions"], [])

    def test_confirmation_is_single_use_and_unverified_adapter_needs_review(self):
        self.store.submit_questionnaire(OWNER, self.case["id"], {"adult_confirmed": True, "pain_score": 2, "activity_limit": 1, "red_flags": []})
        response = SpineHealthAgent(self.store).respond(OWNER, self.case["id"], "我想进行视频步态评估")
        action = response["proposed_actions"][0]

        completed = self.store.confirm_action(OWNER, self.case["id"], action["id"], action["confirmation_token"])
        replay = self.store.confirm_action(OWNER, self.case["id"], action["id"], action["confirmation_token"])
        assessments = self.store.list_assessments(OWNER, self.case["id"])

        self.assertEqual(completed["state"], "needs_review")
        self.assertEqual(replay["state"], "needs_review")
        self.assertEqual(assessments[0]["state"], "needs_review")
        self.assertIn("不会伪造分析结果", assessments[0]["result"]["status_message"])

    def test_quality_and_trend_comparability_are_conservative(self):
        quality = capture_quality(0.9, 0.71, 0.85)
        comparable = measurements_comparable(
            {"protocol_version": "v1", "model_version": "m1", "unit": "degree", "capture_condition": "side", "quality": quality},
            {"protocol_version": "v2", "model_version": "m1", "unit": "degree", "capture_condition": "side", "quality": quality},
        )

        self.assertEqual(quality["score"], 0.71)
        self.assertTrue(quality["eligible"])
        self.assertFalse(comparable["comparable"])
        self.assertIn("protocol_version", comparable["reasons"])

    def test_external_summary_has_no_raw_case_content(self):
        self.store.submit_questionnaire(OWNER, self.case["id"], {"adult_confirmed": True, "pain_score": 3, "activity_limit": 2, "red_flags": []})
        summary = self.store.deidentified_case_summary(OWNER, self.case["id"])
        raw = str(summary)

        self.assertNotIn(self.case["id"], raw)
        self.assertNotIn(OWNER, raw)
        self.assertEqual(summary["case_scope"], "adult_spine_followup")

    def test_agent_reads_compatibility_facts_without_merging_them_into_a_score(self):
        self.store.submit_questionnaire(OWNER, self.case["id"], {"adult_confirmed": True, "pain_score": 2, "activity_limit": 1, "red_flags": []})

        def facts_provider(_user_id, _case_id):
            return {
                "fact_count": 2,
                "modalities": ["既有 DR / X 光链路", "人工临床记录"],
                "facts": [{"source": "既有 DR / X 光链路", "measurements": {"LL": 42.1}}],
            }

        response = SpineHealthAgent(self.store, case_facts_provider=facts_provider).respond(OWNER, self.case["id"], "请整理当前病例的资料")
        fact_card = next(card for card in response["result_cards"] if card["type"] == "case_facts")

        self.assertEqual(fact_card["fact_count"], 2)
        self.assertIn("既有 DR / X 光链路", response["message"]["content"])
        self.assertEqual(response["message"]["audit"]["structured_case_fact_count"], 2)

    def test_structured_knowledge_is_chunked_without_splitting_tables(self):
        prose = "第一段。\n\n" + ("用于验证段落边界的文字。" * 100)
        chunks = chunk_knowledge_sections(
            [
                {"heading": "成人随访", "kind": "prose", "content": prose},
                {"heading": "停止条件", "kind": "table", "content": "动作 | 停止条件\n示例 | 不适加重"},
            ]
        )

        self.assertGreater(len(chunks), 2)
        table = next(chunk for chunk in chunks if chunk["kind"] == "table")
        self.assertEqual(table["content"], "动作 | 停止条件\n示例 | 不适加重")
        self.assertTrue(all(chunk["heading"] == "成人随访" for chunk in chunks if chunk["kind"] == "prose"))

    def test_approved_catalog_can_generate_only_a_catalog_constrained_plan(self):
        self.store.submit_questionnaire(OWNER, self.case["id"], {"adult_confirmed": True, "pain_score": 2, "activity_limit": 1, "red_flags": []})
        self.store.upsert_exercise_catalog_item(
            {
                "id": "reviewed_fixture_move",
                "title": "审核测试动作",
                "audience": "adult",
                "review_status": "approved",
                "definition": {
                    "version": "fixture-v1",
                    "eligibility": {"risk_levels": ["routine"]},
                    "contraindications": [],
                    "stop_conditions": ["出现不适立即停止"],
                    "dose_bounds": {"sets": {"min": 1, "max": 2}},
                },
            },
            reviewed_by="clinical-fixture-reviewer",
        )
        reply = SpineHealthAgent(self.store).respond(OWNER, self.case["id"], "请生成康复计划")
        action = reply["proposed_actions"][0]
        confirmed = self.store.confirm_action(OWNER, self.case["id"], action["id"], action["confirmation_token"])

        self.assertEqual(confirmed["state"], "completed")
        plans = self.store.list_rehab_plans(OWNER, self.case["id"])
        self.assertEqual(plans[0]["plan"]["mode"], "catalog_constrained")
        self.assertEqual(plans[0]["plan"]["items"][0]["exercise_id"], "reviewed_fixture_move")


if __name__ == "__main__":
    unittest.main()
