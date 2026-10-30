from __future__ import annotations

import unittest

from mindspine.personal_cases import PersonalCaseStore, PersonalCaseValidationError


class PersonalCaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = PersonalCaseStore(":memory:")

    def test_profile_is_scoped_to_one_account_and_validated(self):
        profile = self.store.update_profile(
            "user-a",
            {
                "display_name": "小明",
                "birth_date": "2011-05-20",
                "sex": "male",
                "height_cm": 151.5,
                "weight_kg": 42.3,
                "clinician_note": "佩戴支具随访中",
            },
        )

        self.assertEqual(profile.display_name, "小明")
        self.assertEqual(profile.height_cm, 151.5)
        self.assertEqual(self.store.get_case("user-b", default_name="另一位用户")["profile"]["display_name"], "另一位用户")
        with self.assertRaises(PersonalCaseValidationError):
            self.store.update_profile("user-a", {"birth_date": "2999-01-01"})

    def test_entries_generate_separate_ordered_trends(self):
        self.store.create_entry(
            "user-a",
            {
                "kind": "dr",
                "recorded_at": "2026-01-05",
                "title": "DR 复查",
                "payload": {"angles": {"LL": 38.2, "SSA": 31.0, "HARRISON_A": 12.1, "LCI": 7.5}},
            },
        )
        self.store.create_entry(
            "user-a",
            {
                "kind": "clinical",
                "status": "manual",
                "recorded_at": "2026-01-18",
                "payload": {"cobb_angle": 18.4, "atr_angle": 5.5},
            },
        )
        self.store.create_entry(
            "user-a",
            {
                "kind": "clinical",
                "status": "manual",
                "recorded_at": "2026-04-20",
                "payload": {"cobb_angle": 16.2},
            },
        )
        self.store.create_entry(
            "user-a",
            {
                "kind": "checkin",
                "status": "manual",
                "recorded_at": "2026-04-21",
                "payload": {"pain_score": 2, "activity_limit": 1},
            },
        )

        data = self.store.get_case("user-a")

        self.assertEqual(data["overview"]["total_entries"], 4)
        self.assertEqual([point["value"] for point in data["trends"]["dr_ll"]], [38.2])
        self.assertEqual([point["value"] for point in data["trends"]["clinical_cobb"]], [18.4, 16.2])
        self.assertEqual([point["date"] for point in data["trends"]["checkin_pain"]], ["2026-04-21"])
        self.assertNotIn("clinical_cobb", self.store.get_case("user-b")["trends"])

    def test_entry_payload_and_deletion_stay_with_the_owner(self):
        with self.assertRaises(PersonalCaseValidationError):
            self.store.create_entry("user-a", {"kind": "video", "payload": {"value": float("nan")}})

        entry = self.store.create_entry(
            "user-a",
            {"kind": "video", "status": "prepared", "payload": {"duration_seconds": 8.4}},
        )

        self.assertIsNone(self.store.get_entry("user-b", entry["id"]))
        self.assertFalse(self.store.delete_entry("user-b", entry["id"]))
        self.assertTrue(self.store.delete_entry("user-a", entry["id"]))
        self.assertEqual(self.store.get_case("user-a")["overview"]["total_entries"], 0)

    def test_examinations_keep_legacy_rows_and_do_not_mix_other_cases(self):
        old = self.store.create_entry('user-a', {'kind':'clinical','payload':{'cobb_angle':12}})
        current = self.store.create_entry('user-a', {'kind':'dr','case_id':'exam-a','payload':{'angles':{'LL':32}}})
        self.store.create_entry('user-a', {'kind':'dr','case_id':'exam-b','payload':{'angles':{'LL':38}}})
        self.store.create_entry('user-b', {'kind':'dr','case_id':'exam-a','payload':{'angles':{'LL':50}}})
        self.assertEqual(len(self.store.list_entries('user-a')), 3)
        self.assertEqual([e['id'] for e in self.store.list_entries('user-a',case_id='exam-a')], [current['id']])
        self.assertEqual({e['id'] for e in self.store.list_entries('user-a',case_id='exam-a',include_unassigned=True)}, {old['id'],current['id']})

    def test_repeated_image_measurement_updates_only_the_owned_exam_record(self):
        first = self.store.create_entry('user-a', {'kind':'dr','case_id':'a','payload':{'image_id':'image-1','angles':{'LL':30}}})
        revised = self.store.create_entry('user-a', {'kind':'dr','case_id':'a','payload':{'image_id':'image-1','angles':{'LL':32}}})
        separate = self.store.create_entry('user-a', {'kind':'dr','case_id':'b','payload':{'image_id':'image-1','angles':{'LL':35}}})
        self.assertEqual(first['id'], revised['id'])
        self.assertNotEqual(first['id'], separate['id'])
        self.assertEqual(revised['payload']['angles']['LL'], 32)


if __name__ == "__main__":
    unittest.main()
