from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from mindspine.agent_runtime import (ClinicalAgent, grounded_answer, present_answer,
                                    requests_questionnaire, requests_report_export, _questionnaire_context)
from mindspine.spine_agent import SpineAgentPermissionError, SpineAgentStore, SpineAgentValidationError


OWNER = "a" * 32
OTHER = "b" * 32


class FakeGait:
    def __init__(self, case_id):
        self.case_id = case_id
        self.jobs = []
        self.reports = {}
        self.selected = None

    def add(self, job_id, *, selected=False, status="completed", case_id=None):
        actual_case = case_id or self.case_id
        self.jobs.append({"id": job_id, "case_id": actual_case, "status": status,
                          "message": "Synthetic job", "review": {"selected": selected, "archived": selected}})
        self.reports[job_id] = {"job_id": job_id, "case_id": actual_case, "quality": {"passed": True},
                               "analysis": {"summary": "Synthetic result " + job_id, "recommendations": []},
                               "model": {"name": "synthetic_sconet"}}
        if selected:
            self.selected = job_id

    def list(self, user_id, case_id, limit=10):
        return deepcopy(self.jobs[:limit])

    def get(self, user_id, job_id):
        return deepcopy(next(job for job in self.jobs if job["id"] == job_id))

    def report(self, user_id, job_id):
        return deepcopy(self.reports[job_id])

    def selected_result(self, user_id, case_id):
        return self.report(user_id, self.selected) if self.selected else None


class ClinicalAgentTests(unittest.TestCase):
    def setUp(self):
        self.store = SpineAgentStore(":memory:")
        self.case = self.store.create_case(OWNER, "Synthetic case")
        self.case_id = self.case["id"]
        self.store.set_consent(OWNER, self.case_id, True)
        self.knowledge = Mock()
        self.knowledge.search.return_value = [{"id": "source-1", "title": "Synthetic source", "summary": "一般资料需要结合实际检查。", "source_url": "https://example.org/source"}]
        self.gait = FakeGait(self.case_id)
        self.agent = ClinicalAgent(self.store, self.knowledge, self.gait)

    def tearDown(self):
        self.store._memory_connection.close()

    def questionnaire(self, **extra):
        answers = {"consent_confirmed": True, "pain_score": 2, "activity_limit": 1, "red_flags": []}
        answers.update(extra)
        return self.store.submit_questionnaire(OWNER, self.case_id, answers)

    def test_integrated_video_confirmation_has_no_placeholder_review(self):
        action = self.store.propose_action(OWNER, self.case_id, "run_video_screening", "Synthetic rerun", {"source_job_id": "c" * 32})
        first = self.store.confirm_action(OWNER, self.case_id, action["id"], action["confirmation_token"])
        second = self.store.confirm_action(OWNER, self.case_id, action["id"], action["confirmation_token"])
        self.assertEqual(first["state"], "confirmed")
        self.assertEqual(first, second)
        self.assertEqual(self.store.list_assessments(OWNER, self.case_id), [])
        self.assertEqual(self.store.workspace(OWNER, self.case_id)["review_tasks"], [])
        with self.assertRaises(SpineAgentPermissionError):
            self.store.confirm_action(OWNER, self.case_id, action["id"], "wrong token")

    def test_concurrent_confirmation_creates_one_local_review(self):
        parent = Path(__file__).resolve().parents[1] / "temp_uploads"
        root = parent / ("clinical-agent-test-" + uuid.uuid4().hex)
        root.mkdir(parents=True)
        try:
            store = SpineAgentStore(root / "agent.sqlite3")
            case = store.create_case(OWNER, "Synthetic concurrent case")
            action = store.propose_action(OWNER, case["id"], "request_clinical_review", "Synthetic review")
            def confirm(_):
                return store.confirm_action(OWNER, case["id"], action["id"], action["confirmation_token"])
            with ThreadPoolExecutor(max_workers=8) as executor:
                replies = list(executor.map(confirm, range(8)))
            self.assertEqual(len({reply["result"]["review_task"]["id"] for reply in replies}), 1)
            self.assertEqual(len(store.workspace(OWNER, case["id"])["review_tasks"]), 1)
        finally:
            self.assertEqual(root.resolve().parent, parent.resolve())
            shutil.rmtree(root)

    def test_explicit_consent_intake_and_case_mapping(self):
        result = self.questionnaire()
        self.assertEqual(result["triage"]["risk_level"], "routine")
        with self.assertRaises(SpineAgentValidationError):
            self.store.submit_questionnaire(OWNER, self.case_id, {"consent_confirmed": False})
        with self.assertRaises(SpineAgentValidationError):
            self.store.submit_questionnaire(OWNER, self.case_id, {"consent_confirmed": False, "adult_confirmed": True})
        newer = self.store.create_case(OWNER, "Synthetic newer case")
        self.assertTrue(self.store.is_legacy_case(OWNER, self.case_id))
        self.assertFalse(self.store.is_legacy_case(OWNER, newer["id"]))
        self.assertIsNone(self.store.latest_questionnaire(OWNER, newer["id"]))
        with self.assertRaises(SpineAgentPermissionError):
            self.agent.respond(OTHER, self.case_id, "Explain the report")
        self.knowledge.search.assert_not_called()

    def test_long_negated_rerun_does_not_propose_execution(self):
        self.gait.add("old_result")
        for message in ("这是已完成的资料。" * 50 + "不要重新分析，只解释当前报告。",
                        "不用重跑，我只想了解重新分析是什么意思。", "请解释是否需要重新检测。"):
            with self.subTest(message=message[-40:]):
                reply = self.agent.respond(OWNER, self.case_id, message)
                self.assertEqual(reply["proposed_actions"], [])
        affirmative = self.agent.respond(OWNER, self.case_id, "请重新分析本次视频")
        self.assertEqual(affirmative["proposed_actions"][0]["payload"]["source_job_id"], "old_result")

    def test_selected_archived_result_wins_but_new_task_can_be_requested(self):
        self.gait.add("latest", status="running")
        self.gait.add("reviewed", selected=True)
        reply = self.agent.respond(OWNER, self.case_id, "解释步态报告")
        self.assertEqual(reply["workflow"]["job_id"], "reviewed")
        latest = self.agent.respond(OWNER, self.case_id, "查看最新步态任务")
        self.assertEqual(latest["workflow"]["job_id"], "latest")
        self.assertEqual(latest["proposed_actions"], [])

    def test_task_only_requests_do_not_attach_general_medical_sources(self):
        self.gait.add('recorded', selected=True)
        for message in ('请整理本次步态结果和综合报告', '导出本次综合报告', '请重新分析本次视频',
                        '查看历次检查记录', '查看最新步态任务', '解释已有检查'):
            with self.subTest(message=message):
                self.knowledge.search.reset_mock()
                reply = self.agent.respond(OWNER, self.case_id, message)
                self.knowledge.search.assert_not_called()
                self.assertEqual(reply['evidence'], [])
                self.assertNotIn('retrieve_clinical_evidence', reply['workflow']['steps'])

    def test_medical_question_inside_report_request_still_retrieves_knowledge(self):
        reply = self.agent.respond(OWNER, self.case_id, '整理报告，并解释Cobb角是什么意思')
        self.knowledge.search.assert_called_once()
        self.assertTrue(reply['evidence'])

    def test_local_answer_hides_identifiers_but_preserves_sources_and_audit_after_reload(self):
        self.knowledge.search.return_value = [
            {'id': 'who_lbp', 'title': 'WHO 成人腰痛指南', 'summary': '应结合症状和检查资料。',
             'source_url': 'https://www.who.int/publications/i/item/9789240081789'},
            {'id': 'nice_ng59', 'title': 'NICE NG59', 'summary': '资料应由专业人员结合具体情况解读。',
             'source_url': 'https://www.nice.org.uk/guidance/ng59'},
        ]
        self.store.set_consent(OWNER, self.case_id, False)
        original_sources = deepcopy(self.knowledge.search.return_value)
        reply = self.agent.respond(OWNER, self.case_id, '腰痛的指南依据是什么')
        saved = self.store.workspace(OWNER, self.case_id)['messages'][-1]
        self.assertEqual(saved['content'], reply['message']['content'])
        for identifier in ('who_lbp', 'nice_ng59'):
            self.assertNotIn(identifier, saved['content'])
            self.assertIn(identifier, saved['audit']['citation_ids'])
        self.assertEqual([item['source_url'] for item in saved['evidence']],
                         [item['source_url'] for item in original_sources])
        self.assertEqual(self.knowledge.search.return_value, original_sources)

    def test_citation_presentation_preserves_non_citation_brackets_and_links(self):
        sources = [{'id': 'who_lbp'}, {'id': 'nice_ng59'}]
        text = ('内容 [who_lbp]。\n\n另一项 [citation:nice_ng59]。\n'
                '合并来源 [who_lbp; nice_ng59]。\n'
                '保留范围 [0, 10] 和 [L1-L5]、[原始片](local.png)。\n'
                '旧标记链接 [who_lbp](https://example.org/guideline)。')
        rendered = present_answer(text, sources)
        self.assertNotIn('who_lbp', rendered)
        self.assertNotIn('nice_ng59', rendered)
        self.assertIn('内容。\n\n另一项。', rendered)
        self.assertIn('[0, 10] 和 [L1-L5]、[原始片](local.png)', rendered)
        self.assertEqual(present_answer(rendered, sources), rendered)

    def test_task_only_summary_preserves_recorded_urgent_triage(self):
        self.questionnaire(red_flags=['new_bowel_or_bladder_change'])
        reply = self.agent.respond(OWNER, self.case_id, '请整理本次步态结果和综合报告')
        self.knowledge.search.assert_not_called()
        self.assertEqual(reply['safety_status'], 'urgent')
        self.assertEqual(reply['proposed_actions'][0]['kind'], 'request_clinical_review')

    def test_result_cards_survive_case_message_reload(self):
        self.gait.add('persisted_result', selected=True)
        reply = self.agent.respond(OWNER, self.case_id, '请整理本次步态结果和综合报告')
        restored = self.store.workspace(OWNER, self.case_id)['messages'][-1]
        self.assertEqual(restored['audit']['result_cards'], reply['result_cards'])
        self.assertIn('gait_result', {card['type'] for card in restored['audit']['result_cards']})

    def test_report_summary_does_not_reuse_prior_irrelevant_knowledge_text(self):
        self.store.append_agent_message(OWNER, self.case_id, 'assistant', 'OLD_UNRELATED_KNOWLEDGE')
        captured = {}
        def capture(service, question, context, history):
            captured.update(history=history, evidence=context['evidence'])
            return None
        with patch('mindspine.agent_runtime.grounded_answer', side_effect=capture):
            self.agent.respond(OWNER, self.case_id, '整理本次综合报告')
        self.assertEqual(captured, {'history': [], 'evidence': []})

    def test_foreign_case_job_is_never_exposed(self):
        self.gait.add("foreign", case_id="d" * 32, selected=True)
        reply = self.agent.respond(OWNER, self.case_id, "解释步态报告")
        self.assertIsNone(reply["workflow"]["job_id"])
        self.assertFalse(any(card["type"] in {"gait_job", "gait_result"} for card in reply["result_cards"]))

    def test_disabled_consent_and_provider_failures_keep_local_response(self):
        self.store.set_consent(OWNER, self.case_id, False)
        self.agent.report_service = Mock()
        with patch("mindspine.agent_runtime.request.urlopen") as network:
            reply = self.agent.respond(OWNER, self.case_id, "什么是脊柱侧弯")
            network.assert_not_called()
        self.assertFalse(reply["message"]["audit"]["external_model_used"])
        self.store.set_consent(OWNER, self.case_id, True)
        self.agent.report_service.refresh_config.side_effect = RuntimeError("Synthetic unavailable config")
        reply = self.agent.respond(OWNER, self.case_id, "什么是脊柱侧弯")
        self.assertIn("一般资料", reply["message"]["content"])
        self.knowledge.search.side_effect = RuntimeError("Synthetic unavailable index")
        self.agent.facts_provider = lambda *_: {"fact_count": "not a number"}
        reply = self.agent.respond(OWNER, self.case_id, "什么是脊柱侧弯")
        self.assertIn("knowledge", reply["message"]["audit"]["unavailable_sources"])

    def test_external_context_keeps_structured_answers_without_free_text_or_identifiers(self):
        self.questionnaire(symptom_description="PRIVATE_SYMPTOM_TEXT", unrelated_secret="do-not-forward",
                           history="PRIVATE_HISTORY", treatment="PRIVATE_TREATMENT", goals="PRIVATE_GOALS")
        self.agent.report_service = object()
        captured = {}
        def capture(service, question, context, history):
            captured.update(context)
            return None
        with patch("mindspine.agent_runtime.grounded_answer", side_effect=capture):
            self.agent.respond(OWNER, self.case_id, "解释已有检查")
        self.assertEqual(captured["questionnaire"]["answers"]["pain_score"], 2)
        serialized = json.dumps(captured, ensure_ascii=False)
        self.assertNotIn(OWNER, serialized)
        self.assertNotIn(self.case_id, serialized)
        self.assertNotIn("do-not-forward", serialized)
        for private in ('PRIVATE_SYMPTOM_TEXT', 'PRIVATE_HISTORY', 'PRIVATE_TREATMENT', 'PRIVATE_GOALS'):
            self.assertNotIn(private, serialized)

    def test_grounded_output_rejects_cross_modal_score_fusion(self):
        service = SimpleNamespace(refresh_config=lambda: None, api_key="synthetic-test-only",
                                  base_url="https://example.invalid", model="synthetic", timeout_sec=10)
        context = {"evidence": [{"id": "source-1"}], "gait": {"status": "completed"}}
        def answer(text, ids):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps({"choices": [{"message": {"tool_calls": [{"function": {
                "name": "compose_answer", "arguments": json.dumps({"answer": text, "citation_ids": ids})}}]}}]}).encode()
            return response
        for text, ids in (("影像与步态的综合置信度为90%。 [source-1]", ["source-1"]),
                          ("您的患病概率为90%。 [source-1]", ["source-1"]),
                          ("[source-1]", ["source-1"]),
                          ("未知证据 [invented]", ["invented"])):
            with patch("mindspine.agent_runtime.request.urlopen", return_value=answer(text, ids)):
                self.assertIsNone(grounded_answer(service, "解释结果", context, []))
        with patch("mindspine.agent_runtime.request.urlopen", return_value=answer("分别查看影像测量与步态研究分数。 [source-1]", ["source-1"])):
            self.assertIsNotNone(grounded_answer(service, "解释结果", context, []))
        with patch("mindspine.agent_runtime.request.urlopen", return_value=answer("观察记录 [citation:source-1]", ["source-1"])):
            result = grounded_answer(service, "解释结果", context, [])
            self.assertEqual(result['text'], "观察记录")
            self.assertEqual(result['citation_ids'], ['source-1'])
        with patch("mindspine.agent_runtime.request.urlopen", return_value=answer("未知证据 [citation:invented]", ["source-1"])):
            self.assertIsNone(grounded_answer(service, "解释结果", context, []))

    def test_questionnaire_intent_opens_real_persisted_form_not_ad_hoc_score(self):
        for text in ('给我做一份调查问卷', '评估一下我的脊柱健康情况', '我想填写脊柱健康问卷'):
            with self.subTest(text=text), patch('mindspine.agent_runtime.grounded_answer') as model:
                reply = self.agent.respond(OWNER, self.case_id, text)
                card = next(c for c in reply['result_cards'] if c['type'] == 'questionnaire')
                self.assertIn(self.case_id, card['url'])
                self.assertIn('不把不同项目混成一个健康总分', reply['message']['content'])
                self.assertIsNone(self.store.latest_questionnaire(OWNER, self.case_id))
                self.assertEqual(reply['message']['audit']['result_cards'], reply['result_cards'])
                model.assert_not_called()
        for text in ('不要填写问卷', '问卷依据是什么', '解释问卷分析', '如何设计调查问卷', '导出问卷 PDF 报告'):
            self.assertFalse(requests_questionnaire(text), text)

    def test_report_generation_requires_explicit_action(self):
        for text in ('给我生成一份健康档案', '导出简洁版 PDF 报告', '制作给医生看的评估报告', '请保存为 Word',
                     '帮我做份健康报告', '帮我做个 PDF 报告', '帮我出份 Word 档案'):
            self.assertTrue(requests_report_export(text), text)
        for text in ('解释我的报告', '不用导出报告', '报告里的 Cobb 是什么意思'):
            self.assertFalse(requests_report_export(text), text)

    def test_questionnaire_report_export_does_not_reopen_intake_or_call_model(self):
        self.store.set_consent(OWNER, self.case_id, False)
        with patch('mindspine.agent_runtime.grounded_answer') as model:
            reply = self.agent.respond(OWNER, self.case_id, '仅导出问卷的简洁版 PDF 报告给医生')
        types = [card['type'] for card in reply['result_cards']]
        self.assertNotIn('questionnaire', types)
        card = next(card for card in reply['result_cards'] if card['type'] == 'report_export')
        self.assertEqual(card['options']['format'], 'pdf')
        self.assertEqual(card['options']['sections'], ['questionnaire'])
        self.assertEqual(card['options']['audience'], 'clinician')
        self.assertEqual(card['options']['detail'], 'brief')
        self.assertEqual(reply['message']['audit']['result_cards'], reply['result_cards'])
        model.assert_not_called()

    def test_no_evidence_uses_model_general_without_case_context_or_fake_sources(self):
        self.knowledge.search.return_value = []
        self.questionnaire(symptom_description='CASE_SECRET')
        self.store.append_agent_message(OWNER, self.case_id, 'assistant', 'OLD_PRIVATE_FACT')
        with patch('mindspine.agent_runtime.grounded_answer', return_value={
                'text':'日常活动与症状变化可以记录下来，供面诊讨论。', 'citation_ids': []}) as rewrite:
            reply = self.agent.respond(OWNER, self.case_id, '腰痛时需要注意什么')
        self.assertEqual(reply['answer_mode'], 'model_general')
        self.assertEqual(rewrite.call_args.args[2], {'answer_mode': 'model_general', 'evidence': []})
        self.assertEqual(rewrite.call_args.args[3], [])
        self.assertEqual(reply['evidence'], [])
        self.assertTrue(reply['message']['audit']['external_model_used'])
        self.assertEqual(reply['message']['audit']['citation_ids'], [])

    def test_no_evidence_without_consent_never_sends_question_or_searches(self):
        self.store.set_consent(OWNER, self.case_id, False)
        self.knowledge.search.return_value = []
        self.agent.online_search = Mock()
        with patch('mindspine.agent_runtime.grounded_answer') as model:
            reply = self.agent.respond(OWNER, self.case_id, '请联网查一下腰痛', web_search=True)
        self.agent.online_search.assert_not_called()
        model.assert_not_called()
        self.assertEqual(reply['answer_mode'], 'unavailable')
        self.assertEqual(reply['message']['audit']['web_search']['status'], 'consent_required')
        self.assertIn('consent_request', [card['type'] for card in reply['result_cards']])

    def test_web_search_is_opt_in_and_keeps_real_sources_in_audit(self):
        self.knowledge.search.return_value = []
        source = {'id': 'pubmed_123', 'title': 'Synthetic adult back pain review',
                  'source_url': 'https://pubmed.ncbi.nlm.nih.gov/123/', 'excerpt': 'Synthetic excerpt',
                  'review_status': 'online_unreviewed'}
        self.agent.online_search = Mock(return_value={'status': 'completed', 'evidence': [source]})
        with patch('mindspine.agent_runtime.grounded_answer', return_value={
                'text':'片段只提供部分信息。', 'citation_ids':['pubmed_123']}):
            reply = self.agent.respond(OWNER, self.case_id, '腰痛的证据是什么', web_search=True)
        self.assertEqual(reply['answer_mode'], 'web_evidence')
        self.assertEqual(reply['evidence'], [source])
        self.assertEqual(reply['message']['audit']['web_search']['result_count'], 1)
        self.agent.online_search.reset_mock()
        with patch('mindspine.agent_runtime.grounded_answer', return_value=None):
            self.agent.respond(OWNER, self.case_id, '腰痛的证据是什么')
        self.agent.online_search.assert_not_called()

    def test_web_failure_uses_general_model_and_explicit_no_online_notice(self):
        self.knowledge.search.return_value = []
        self.agent.online_search = Mock(side_effect=TimeoutError())
        with patch('mindspine.agent_runtime.grounded_answer', return_value={
                'text': '以下只能介绍一般概念。', 'citation_ids': []}):
            reply = self.agent.respond(OWNER, self.case_id, '腰痛是什么意思', web_search=True)
        self.assertEqual(reply['answer_mode'], 'model_general')
        self.assertIn('本次没有使用联网来源', reply['message']['content'])
        self.assertEqual(reply['evidence'], [])
        self.assertIn('online_search', reply['message']['audit']['unavailable_sources'])

    def test_urgent_triage_blocks_model_and_online_not_questionnaire_review_card(self):
        self.questionnaire(red_flags=['new_bowel_or_bladder_change'])
        self.agent.online_search = Mock()
        with patch('mindspine.agent_runtime.grounded_answer') as model:
            reply = self.agent.respond(OWNER, self.case_id, '评估一下我的脊柱健康情况', web_search=True)
        self.assertEqual(reply['safety_status'], 'urgent')
        self.assertEqual(reply['answer_mode'], 'workflow')
        self.assertEqual(reply['message']['audit']['web_search']['status'], 'blocked_safety')
        model.assert_not_called()
        self.agent.online_search.assert_not_called()

    def test_general_model_rejects_invented_sources_but_accepts_clinical_brackets(self):
        service = SimpleNamespace(refresh_config=lambda: None, api_key='synthetic',
                                  base_url='https://example.invalid', model='synthetic', timeout_sec=5)
        def response_for(text, ids):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps({'choices':[{'message':{'tool_calls':[{'function':{
                'name':'compose_answer', 'arguments':json.dumps({'answer':text,'citation_ids':ids})}}]}}]}).encode()
            return response
        context = {'answer_mode':'model_general', 'evidence':[]}
        for text, ids in (('一般说明 [made_up]', ['made_up']), ('来源 https://example.org', []), ('检索发现可以治愈', [])):
            with patch('mindspine.agent_runtime.request.urlopen', return_value=response_for(text, ids)):
                self.assertIsNone(grounded_answer(service, '腰痛', context, []))
        with patch('mindspine.agent_runtime.request.urlopen', return_value=response_for('记录疼痛范围 [0, 10]，而不是做诊断。', [])):
            result = grounded_answer(service, '腰痛', context, [])
            self.assertIn('[0, 10]', result['text'])

    def test_v3_external_summary_is_structured_only(self):
        context = _questionnaire_context({'protocol_version': 'spine-intake-v3', 'answers': {
            'adult_confirmed': True, 'pain_score': 3, 'symptom_location': ['腰部'],
            'daily_function': {'walking': 'mild', 'sleep': 'PRIVATE_INJECTED_VALUE'},
            'red_flag_answers': {'saddle_numbness': 'unknown', 'arbitrary_key': 'yes'},
            'history': 'PRIVATE_HISTORY', 'treatment': 'PRIVATE_TREATMENT', 'goals': 'PRIVATE_GOALS',
            'symptom_notes': 'PRIVATE_SYMPTOMS', 'red_flags': ['saddle_numbness', 'PRIVATE_FLAG']},
            'triage': {'risk_level': 'incomplete'}})
        self.assertEqual(context['answers']['daily_function'], {'walking': 'mild'})
        self.assertEqual(context['answers']['red_flag_answers'], {'saddle_numbness': 'unknown'})
        self.assertEqual(context['answers']['symptom_location'], ['腰部'])
        self.assertNotIn('PRIVATE', json.dumps(context))
        self.assertNotIn('arbitrary_key', json.dumps(context))

    def test_grounded_network_timeout_is_bounded_and_failure_is_recoverable(self):
        service = SimpleNamespace(refresh_config=lambda: None, api_key='synthetic',
                                  base_url='https://example.invalid', model='synthetic', timeout_sec=180)
        with patch('mindspine.agent_runtime.request.urlopen', side_effect=TimeoutError()) as network:
            self.assertIsNone(grounded_answer(service, '一般知识', {'answer_mode':'model_general', 'evidence':[]}, []))
        self.assertEqual(network.call_args.kwargs['timeout'], 35)


if __name__ == "__main__":
    unittest.main()
