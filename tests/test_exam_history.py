from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from mindspine.agent_runtime import ClinicalAgent, _questionnaire_context
from mindspine.exam_history import ExamHistoryProvider, requests_history, history_context, describe_history
from mindspine.personal_cases import PersonalCaseStore
from mindspine.spine_agent import SpineAgentStore, SpineAgentPermissionError

OWNER, OTHER = 'a' * 32, 'b' * 32


class HistoryGait:
    def __init__(self):
        self.jobs, self.reports = [], {}

    def add(self, case_id, job_id, *, owner=OWNER, archived=True, passed=True, selected=True,
            version='2.0.1', status='completed', demo=False):
        self.jobs.append({'id': job_id, 'owner': owner, 'case_id': case_id, 'status': status,
                          'created_at': 1700000000, 'engine_version': version,
                          'review': {'archived': archived, 'selected': selected}})
        self.reports[job_id] = {'job_id': job_id, 'case_id': case_id, 'status': status,
                               'quality': {'passed': passed, 'valid_frames': 45,
                                           'issues': [{'code': 'occlusion', 'message': 'DO_NOT_EXPORT'}]},
                               'model': {'model_name': 'ScoNet-MT', 'checkpoint_sha256': 'c' * 64},
                               'analysis': {'model_scores': {'negative': .2, 'neutral': .3, 'positive': .5}},
                               'research_demo': demo, 'private_path': 'DO_NOT_EXPORT'}

    def list(self, user_id, case_id, limit=50):
        return deepcopy([job for job in self.jobs if job['owner'] == user_id and job['case_id'] == case_id][:limit])

    def report(self, user_id, job_id):
        job = next(job for job in self.jobs if job['id'] == job_id)
        if job['owner'] != user_id:
            raise ValueError('denied')
        return deepcopy(self.reports[job_id])

    def selected_result(self, user_id, case_id):
        return None


class ExamHistoryTests(unittest.TestCase):
    def setUp(self):
        self.store, self.personal = SpineAgentStore(':memory:'), PersonalCaseStore(':memory:')
        self.case = self.store.create_case(OWNER, 'own exam')
        self.store.set_consent(OWNER, self.case['id'], True)
        self.other_case = self.store.create_case(OTHER, 'other exam')
        self.gait = HistoryGait()
        self.provider = ExamHistoryProvider(self.store, self.gait, self.personal)

    def tearDown(self):
        self.store._memory_connection.close()
        self.personal._memory_connection.close()

    def questionnaire(self, case=None, pain=4):
        return self.store.submit_questionnaire(OWNER, (case or self.case)['id'],
                                              {'consent_confirmed': True, 'pain_score': pain, 'activity_limit': 2})

    def entry(self, *, owner=OWNER, case=None, value=18, status='completed'):
        return self.personal.create_entry(owner, {'case_id': (case or self.case)['id'], 'kind': 'dr',
                'status': status, 'recorded_at': '2026-08-01', 'source_filename': 'DO_NOT_EXPORT',
                'payload': {'angles': {'cobb_angle': value}, 'note': 'DO_NOT_EXPORT'}})

    def test_cross_account_and_shared_cases_are_not_owner_history(self):
        self.questionnaire()
        self.entry()
        self.entry(owner=OTHER, case=self.other_case, value=80)
        self.gait.add(self.other_case['id'], 'other_job', owner=OTHER)
        self.store.invite_member(OTHER, self.other_case['id'], OWNER, 'clinician', None)
        history = self.provider(OWNER, self.case['id'])
        self.assertEqual(history['record_count'], 2)
        self.assertTrue(all(row['case_id'] == self.case['id'] for row in history['records']))
        self.assertEqual(self.provider(OWNER, self.other_case['id'])['records'], [])
        with self.assertRaises(SpineAgentPermissionError):
            self.provider(OTHER, self.case['id'])

    def test_unarchived_and_pending_results_are_excluded(self):
        self.gait.add(self.case['id'], 'unarchived', archived=False)
        self.gait.add(self.case['id'], 'pending', status='running')
        self.entry(status='prepared')
        self.assertEqual(self.provider(OWNER, self.case['id'])['records'], [])

    def test_one_adopted_gait_per_exam_and_versions_quality_retained(self):
        self.gait.add(self.case['id'], 'rerun', selected=False, version='2.0.0')
        self.gait.add(self.case['id'], 'adopted', version='2.0.1')
        rows = self.provider(OWNER, self.case['id'])['records']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['record_id'], 'adopted')
        self.assertEqual(rows[0]['engine_version'], '2.0.1')
        self.assertEqual(rows[0]['model_scores']['positive'], .5)
        self.assertEqual(rows[0]['quality']['issue_codes'], ['occlusion'])

    def test_rejected_or_demo_scores_cannot_become_patient_trends(self):
        for status, passed, demo in [('rejected', False, False), ('completed', True, True)]:
            with self.subTest(status=status, demo=demo):
                self.gait = HistoryGait()
                self.gait.add(self.case['id'], 'job', status=status, passed=passed, demo=demo)
                rows = ExamHistoryProvider(self.store, self.gait)(OWNER, self.case['id'])['records']
                self.assertEqual(rows[0]['model_scores'], {})

    def test_no_data_returns_no_invented_trend_and_context_is_identifier_free(self):
        empty = self.provider(OWNER, self.case['id'])
        self.assertFalse(empty['clinical_trend_inferred'])
        self.assertIn('无法判断', describe_history(empty))
        self.questionnaire()
        self.entry()
        self.gait.add(self.case['id'], 'private_job_id')
        history = self.provider(OWNER, self.case['id'])
        safe = json.dumps(history_context(history), ensure_ascii=False)
        for forbidden in (self.case['id'], OWNER, 'private_job_id', 'DO_NOT_EXPORT', 'record_id', 'case_id'):
            self.assertNotIn(forbidden, safe)

    def test_any_included_case_consent_prevents_external_history(self):
        old = self.store.create_case(OWNER, 'older exam')
        self.questionnaire(old)
        self.store.set_consent(OWNER, old['id'], False)
        self.assertFalse(self.provider(OWNER, self.case['id'])['external_ai_allowed'])

    def test_requests_are_explicit_and_negation_does_not_load_history(self):
        for message in ('请比较历次检查', '我的步态变化趋势', '和上次比较怎么样', '随访对比'):
            self.assertTrue(requests_history(message), message)
        for message in ('不要读取历史记录', '什么是趋势分析', '什么是随访对比', '解释本次步态', '什么是侧弯'):
            self.assertFalse(requests_history(message), message)

    def test_default_agent_never_calls_history_but_explicit_request_has_card(self):
        knowledge = Mock()
        knowledge.search.return_value = []
        provider = Mock(wraps=self.provider)
        agent = ClinicalAgent(self.store, knowledge, self.gait, history_provider=provider)
        agent.respond(OWNER, self.case['id'], '什么是侧弯')
        provider.assert_not_called()
        reply = agent.respond(OWNER, self.case['id'], '请对比历次检查')
        provider.assert_called_once_with(OWNER, self.case['id'])
        self.assertTrue(any(card['type'] == 'case_history' for card in reply['result_cards']))
        self.assertIn('无法判断', reply['message']['content'])

    def test_history_card_is_persisted_with_bounded_records(self):
        self.questionnaire()
        knowledge = Mock()
        knowledge.search.return_value = []
        result = self.provider(OWNER, self.case['id'])
        result['records'] = result['records'] * 120
        agent = ClinicalAgent(self.store, knowledge, self.gait, history_provider=lambda *_: result)
        agent.respond(OWNER, self.case['id'], '查看历次检查记录')
        restored = self.store.workspace(OWNER, self.case['id'])['messages'][-1]
        card = next(card for card in restored['audit']['result_cards'] if card['type'] == 'case_history')
        self.assertEqual(len(card['records']), 100)
        self.assertTrue(card['truncated'])

    def test_nonconsenting_history_cannot_leak_through_later_chat_memory(self):
        self.questionnaire()
        old = self.store.create_case(OWNER, 'old')
        self.questionnaire(old, pain=8)
        self.store.set_consent(OWNER, old['id'], False)
        knowledge = Mock()
        knowledge.search.return_value = []
        agent = ClinicalAgent(self.store, knowledge, self.gait, history_provider=self.provider)
        with patch('mindspine.agent_runtime.grounded_answer', return_value=None) as rewrite:
            first = agent.respond(OWNER, self.case['id'], '对比历次检查')
            rewrite.assert_not_called()
            agent.respond(OWNER, self.case['id'], '解释本次情况')
            memory = rewrite.call_args.args[3]
        self.assertNotIn(first['message']['content'], [item['content'] for item in memory])

    def test_frontend_questionnaire_only_sends_structured_fields_without_record_ids(self):
        fields = {'duration': '1–3 个月', 'daily_function': {'walking': 'mild'}, 'history': ['旧伤'],
                  'treatment': ['观察'], 'goals': ['减少疼痛'], 'case_id': 'private'}
        context = _questionnaire_context({'answers': fields, 'id': 'private'})
        for field in ('duration', 'daily_function'):
            self.assertEqual(context['answers'][field], fields[field])
        for field in ('history', 'treatment', 'goals'):
            self.assertNotIn(field, context['answers'])
        self.assertNotIn('private', json.dumps(context))


if __name__ == '__main__':
    unittest.main()
