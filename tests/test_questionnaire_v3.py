from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from mindspine.questionnaire import RED_FLAGS, VERSION, normalize_answers, questionnaire_schema, triage_answers
from mindspine.spine_agent import SpineAgentPermissionError, SpineAgentStore, SpineAgentValidationError


OWNER, OTHER = "a" * 32, "b" * 32


def complete_answers():
    return {
        "adult_confirmed": "yes", "consent_confirmed": True,
        "red_flag_answers": {key: "no" for key in RED_FLAGS},
        "symptom_location": ["腰部"], "duration": "1–4 周", "symptom_change": "基本不变",
        "radiation": "没有", "pain_score": 2,
        "daily_function": {key: "none" for key in ("walking", "sitting", "sleep", "dressing")},
        "history_status": "no", "treatment_status": "no", "goals": "记录疼痛与生活变化",
    }


class QuestionnaireV3Tests(unittest.TestCase):
    def setUp(self):
        self.store = SpineAgentStore(":memory:")
        self.case_id = self.store.ensure_default_case(OWNER)["id"]

    def submit(self, answers):
        return self.store.submit_questionnaire(OWNER, self.case_id, answers, VERSION)

    def test_schema_has_plain_language_anchors_function_options_and_sources(self):
        schema = questionnaire_schema()
        self.assertEqual(schema["version"], VERSION)
        self.assertEqual(schema["review_status"], "not_clinically_validated")
        fields = {f["id"]: f for section in schema["sections"] for f in section["fields"]}
        self.assertIn("走路", fields["pain_score"]["anchors"][2]["detail"])
        self.assertIn("穿衣", fields["daily_function.dressing"]["label"])
        self.assertEqual(len([key for key in fields if key.startswith("red_flag_answers.")]), len(RED_FLAGS))
        self.assertTrue(all(source["url"].startswith("https://") for source in schema["sources"]))

    def test_record_is_persisted_and_retrievable_in_case(self):
        record = self.submit(complete_answers())
        loaded = self.store.workspace(OWNER, self.case_id)["questionnaire"]
        self.assertEqual(loaded["id"], record["id"])
        self.assertEqual(loaded["protocol_version"], VERSION)
        self.assertEqual(loaded["answers"]["pain_score"], 2)
        self.assertEqual(loaded["triage"]["risk_level"], "routine")
        self.assertIn("睡眠：无影响", loaded["triage"]["analysis"])
        self.assertIsNone(loaded["triage"]["activity_limit"])
        self.assertTrue(loaded["triage"]["not_a_diagnosis"])
        self.assertNotIn("total_score", loaded["triage"])

    def test_unknown_never_becomes_no_or_allows_automation(self):
        for path in ("red_flag_answers.saddle_numbness", "daily_function.sleep", "history_status", "treatment_status"):
            with self.subTest(path=path):
                answers = complete_answers()
                keys = path.split(".")
                if len(keys) == 1:
                    answers[keys[0]] = "unknown"
                else:
                    answers[keys[0]][keys[1]] = "unknown"
                record = self.submit(answers)
                self.assertEqual(record["triage"]["risk_level"], "incomplete")
                self.assertFalse(record["triage"]["automation_allowed"])
                self.assertTrue(record["triage"]["missing_or_uncertain"])

    def test_missing_required_answer_is_rejected_even_without_browser_validation(self):
        for field in ("adult_confirmed", "red_flag_answers", "symptom_location", "duration", "pain_score", "daily_function", "history_status", "treatment_status", "goals"):
            with self.subTest(field=field):
                answers = complete_answers()
                answers.pop(field)
                with self.assertRaises(SpineAgentValidationError):
                    self.submit(answers)

    def test_whitespace_is_not_a_required_text_answer(self):
        answers = complete_answers()
        answers["goals"] = "   "
        with self.assertRaises(SpineAgentValidationError):
            self.submit(answers)

    def test_history_and_current_treatment_require_details_when_yes(self):
        for status, detail in (("history_status", "history"), ("treatment_status", "treatment")):
            with self.subTest(status=status):
                answers = complete_answers()
                answers[status] = "yes"
                with self.assertRaises(SpineAgentValidationError):
                    self.submit(answers)
                answers[detail] = "具体名称不记得，需要向医生核对"
                triage = self.submit(answers)["triage"]
                self.assertEqual(triage["risk_level"], "attention")
                self.assertFalse(triage["automation_allowed"])

    def test_red_flag_max_precedes_unknown_and_normal_pain(self):
        answers = complete_answers()
        answers["pain_score"] = 0
        answers["red_flag_answers"].update(saddle_numbness="yes", fever_or_infection="yes", night_pain="yes")
        answers["history_status"] = "unknown"
        record = self.submit(answers)
        self.assertEqual(record["triage"]["risk_level"], "urgent")
        self.assertEqual(record["triage"]["max_rule_severity"], 3)
        self.assertFalse(record["triage"]["automation_allowed"])
        self.assertEqual(len(self.store.workspace(OWNER, self.case_id)["review_tasks"]), 1)

    def test_each_red_flag_follows_versioned_severity(self):
        for key, (severity, *_rest) in RED_FLAGS.items():
            with self.subTest(key=key):
                answers = complete_answers()
                answers["red_flag_answers"][key] = "yes"
                result = triage_answers(normalize_answers(answers))
                self.assertEqual(result["max_rule_severity"], severity)
                self.assertFalse(result["automation_allowed"])

    def test_non_adult_answers_never_enable_adult_automation(self):
        answers = complete_answers()
        answers["adult_confirmed"] = "no"
        record = self.submit(answers)
        self.assertFalse(record["triage"]["automation_allowed"])
        self.assertEqual(record["triage"]["risk_level"], "incomplete")

    def test_uncertain_radiation_and_contradictory_pain_need_clarification(self):
        answers = complete_answers()
        answers["radiation"] = "不确定"
        self.assertEqual(self.submit(answers)["triage"]["risk_level"], "incomplete")
        answers = complete_answers()
        answers["symptom_location"] = ["当前无明显不适"]
        triage = self.submit(answers)["triage"]
        self.assertFalse(triage["automation_allowed"])
        self.assertIn("不一致", "".join(triage["missing_or_uncertain"]))

    def test_trigger_severities_are_independent_of_other_red_flags(self):
        answers = complete_answers()
        answers["red_flag_answers"]["saddle_numbness"] = "yes"
        answers["symptom_change"] = "逐渐加重"
        triage = self.submit(answers)["triage"]
        self.assertEqual(triage["max_rule_severity"], 3)
        worsening = next(item for item in triage["triggers"] if item["id"] == "worsening_symptoms")
        self.assertEqual(worsening["severity"], 1)

    def test_malformed_and_nonfinite_values_rejected(self):
        for value in (True, -1, 11, 1.5, float("nan"), float("inf"), "2"):
            with self.subTest(value=value):
                answers = complete_answers()
                answers["pain_score"] = value
                with self.assertRaises(SpineAgentValidationError):
                    self.submit(answers)
        answers = complete_answers()
        answers["symptom_location"] = ["腰部", "当前无明显不适"]
        with self.assertRaises(SpineAgentValidationError):
            self.submit(answers)

    def test_same_second_update_is_latest_but_prior_record_remains(self):
        with patch.object(self.store, "_now", return_value=1700000000):
            first = self.submit(complete_answers())
            answers = complete_answers()
            answers["pain_score"] = 4
            second = self.submit(answers)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.store.latest_questionnaire(OWNER, self.case_id)["id"], second["id"])
        with self.store._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM questionnaire_responses WHERE case_id=?", (self.case_id,)).fetchone()[0], 2)
            self.assertEqual(self.store._latest_questionnaire_unchecked(conn, self.case_id)["id"], second["id"])

    def test_legacy_record_unchanged_when_v3_is_submitted(self):
        old_answers = {"adult_confirmed": True, "pain_score": 1, "activity_limit": 2, "red_flags": []}
        old = self.store.submit_questionnaire(OWNER, self.case_id, old_answers)
        self.submit(complete_answers())
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM questionnaire_responses WHERE id=?", (old["id"],)).fetchone()
        retained = self.store._questionnaire_row(row)
        self.assertEqual(retained["answers"], old_answers)
        self.assertEqual(retained["protocol_version"], "spine-intake-v2")

    def test_no_cross_case_write_and_no_unsupported_protocol(self):
        with self.assertRaises(SpineAgentPermissionError):
            self.store.submit_questionnaire(OTHER, self.case_id, complete_answers(), VERSION)
        with self.assertRaises(SpineAgentValidationError):
            self.store.submit_questionnaire(OWNER, self.case_id, complete_answers(), "arbitrary-version")

    def test_new_cases_require_explicit_external_consent(self):
        self.assertFalse(self.store.get_consent(OWNER, self.case_id)["external_ai_allowed"])
        self.store.set_consent(OWNER, self.case_id, True)
        self.store.ensure_default_case(OWNER)
        self.assertTrue(self.store.get_consent(OWNER, self.case_id)["external_ai_allowed"])
        second = self.store.create_case(OWNER, "第二份病例")
        self.assertFalse(self.store.get_consent(OWNER, second["id"])["external_ai_allowed"])


if __name__ == "__main__":
    unittest.main()
