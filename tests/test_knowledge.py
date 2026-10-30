from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from mindspine.knowledge import KnowledgeIndex, decode_sources, validate_sources, merge_sources, import_sources, main


class KnowledgeRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = KnowledgeIndex()

    def test_chinese_long_question_recovers_bracing_evidence(self):
        results = self.index.search('孩子检查发现脊柱侧弯，现在想知道支具为什么要每天佩戴那么长时间', limit=4)
        self.assertTrue({'srs_bracing', 'braist_2013'} & {item['id'] for item in results})
        self.assertTrue(all(item['citation']['url'] == item['source_url'] for item in results))

    def test_adult_population_does_not_receive_growth_only_treatment(self):
        results = self.index.search('成年后侧弯伴有腰腿疼痛，想知道康复和手术如何选择', population='adult')
        self.assertEqual(results[0]['id'], 'nhs_adult_treatment')
        self.assertNotIn('srs_bracing', {item['id'] for item in results})
        self.assertTrue(all('adult' in item['populations'] or 'all' in item['populations'] for item in results))

    def test_red_flag_query_retrieves_immediate_assessment(self):
        results = self.index.search('突然腰痛并且尿不出来还伴有会阴麻木，能等视频步态分析后再决定吗', limit=3)
        self.assertEqual(results[0]['id'], 'nice_ng127')
        self.assertIn('立即', results[0]['summary'])

    def test_mri_alias_and_accurate_snippet(self):
        results = self.index.search('核磁检查有辐射吗，有植入物怎么办', limit=1)
        self.assertEqual(results[0]['id'], 'radiology_spine_mri')
        self.assertIn(results[0]['snippet'], [results[0]['summary'], *results[0]['key_points']])

    def test_audience_topic_and_usage_filters_apply_together(self):
        results = self.index.search('侧弯支具康复和 PSSE', audience='family', topic='bracing', usage='education')
        self.assertTrue(results)
        self.assertTrue(all('family' in doc['audiences'] and 'bracing' in doc['topics'] and
                            'education' in doc['usage'] for doc in results))
        self.assertNotIn('sosort_2016', {item['id'] for item in results})
        self.assertEqual(self.index.search('ScoNet视频步态识别', usage='research_reference')[0]['id'], 'sconet_paper')
        self.assertNotIn('sconet_paper', {item['id'] for item in self.index.search('ScoNet视频步态识别', usage='clinical_reference')})

    def test_empty_and_unrelated_queries_do_not_return_padding(self):
        for query in ('', '   ', '请问我该怎么办', '今天北京的天气怎么样',
                      '如何把Python多线程变成异步任务', '成人高考数学复习', '番茄炒蛋怎么做好吃'):
            with self.subTest(query=query):
                self.assertEqual(self.index.search(query), [])

    def test_evidence_provenance_and_coverage_are_explicit(self):
        stats = self.index.stats()
        self.assertGreaterEqual(stats['source_count'], 50)
        self.assertLessEqual(stats['source_count'], 60)
        self.assertEqual(stats['source_count'], len({doc['source_url'] for doc in self.index.documents}))
        self.assertFalse(stats['clinically_signed_off'])
        for doc in self.index.documents:
            self.assertEqual(doc['evidence_kind'], 'knowledge')
            self.assertEqual(doc['access_mode'], 'summary_only')
            self.assertFalse(doc['full_text_indexed'])
            for field in ('publisher', 'retrieved_at', 'retrieval_method', 'source_license',
                          'limitations', 'evidence_level', 'applicability'):
                self.assertTrue(doc[field])

    def test_mutating_returned_evidence_does_not_change_index(self):
        result = self.index.search('支具', limit=1)[0]
        original_id = result['id']
        result['summary'] = 'fabricated patient fact'
        result['topics'].append('fabricated')
        fresh = next(doc for doc in self.index.search('支具', limit=10) if doc['id'] == original_id)
        self.assertNotEqual(fresh['summary'], result['summary'])
        self.assertNotIn('fabricated', fresh['topics'])

    def test_case_facts_cannot_be_loaded_as_knowledge(self):
        record = deepcopy(self.index.documents[0])
        record['evidence_kind'] = 'patient_fact'
        payload = json.dumps({'sources': [record]}, ensure_ascii=False).encode('utf-8')
        with patch('mindspine.knowledge.Path.read_bytes', return_value=payload):
            with self.assertRaisesRegex(ValueError, 'Patient facts'):
                KnowledgeIndex()

    def test_requested_limit_and_invalid_audience(self):
        self.assertEqual(len(self.index.search('侧弯', limit=1)), 1)
        self.assertEqual(self.index.search('侧弯', limit=0), [])
        with self.assertRaises(ValueError):
            self.index.search('侧弯', audience='unsupported-role')

    def test_school_screening_and_measurement_queries_find_chinese_policy(self):
        results = self.index.search('学校每年的脊柱筛查发现异常后，怎样建立学生档案和转介医院',
                                    topic='school_health', limit=4)
        self.assertIn('china_five_health_2026', {doc['id'] for doc in results})
        self.assertTrue(all('school_health' in doc['topics'] for doc in results))
        measurement = self.index.search('Cobb角以外的矢状平衡和椎体旋转参数有什么意义',
                                       audience='doctor', topic='measurement', limit=2)
        self.assertEqual(measurement[0]['id'], 'srs_radiographic_manual')

    def test_general_gait_query_does_not_imply_cancer_or_postoperative_context(self):
        for query in ('了解一般步态和平衡评估资料', '请整理本次步态结果和综合报告', '走路不稳的步态评估'):
            with self.subTest(query=query):
                results = self.index.search(query, limit=20)
                self.assertNotIn('nice_ng234', {doc['id'] for doc in results})
                self.assertNotIn('gstt_postop_recovery', {doc['id'] for doc in results})

    def test_explicit_cancer_gait_and_postoperative_questions_keep_specific_evidence(self):
        cancer = self.index.search('肿瘤病史伴新发步态障碍和肢体无力，需要警惕脊髓压迫吗', limit=3)
        self.assertEqual(cancer[0]['id'], 'nice_ng234')
        postoperative = self.index.search('腰椎减压术后怎样逐步恢复步行和安排复查', limit=3)
        self.assertIn('gstt_postop_recovery', {doc['id'] for doc in postoperative})

    def test_jsonl_bom_blank_lines_and_envelope_produce_same_records(self):
        docs = self.index.documents[:2]
        lines = '\ufeff\n' + '\n\n'.join(json.dumps(doc, ensure_ascii=False) for doc in docs)
        _, actual = decode_sources(lines.encode('utf-8'), jsonl=True)
        self.assertEqual(actual, docs)
        _, enclosed = decode_sources(json.dumps({'sources': docs}).encode())
        self.assertEqual(actual, enclosed)

    def test_metadata_validation_rejects_bad_dates_and_invented_signoff(self):
        for field, value in [('retrieved_at', '2026-02-30'), ('source_date', 'unknown'),
                             ('audiences', ['parent']), ('topics', 'scoliosis'),
                             ('source_url', 'https://secret@example.com/source'),
                             ('source_url', 'javascript:alert(1)'), ('clinically_signed_off', True),
                             ('full_text_indexed', True)]:
            with self.subTest(field=field, value=value):
                record = deepcopy(self.index.documents[0])
                record[field] = value
                with self.assertRaises(ValueError):
                    validate_sources([record])

    def test_import_merge_is_idempotent_and_does_not_mutate_inputs(self):
        existing, incoming = self.index.documents[:1], self.index.documents[:2]
        before = deepcopy(existing)
        merged, report = merge_sources(existing, incoming)
        self.assertEqual(report, {'added': 1, 'skipped': 1, 'source_count': 2})
        self.assertEqual(existing, before)
        self.assertEqual(merge_sources(merged, incoming)[1]['added'], 0)

    def test_import_rejects_conflicting_id_url_and_duplicate_sources(self):
        base = self.index.documents[0]
        for changes in ({'summary': 'altered'}, {'id': 'different_id'}):
            duplicate = {**deepcopy(base), **changes}
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                merge_sources([base], [duplicate])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            validate_sources([base, {**deepcopy(base), 'id': 'different_id',
                                    'source_url': base['source_url'] + '/#section'}])

    def test_import_preview_has_no_filesystem_mutations(self):
        payload = json.dumps(self.index.documents[:1]).encode()
        with patch('mindspine.knowledge.Path.read_bytes', return_value=payload), \
             patch('mindspine.knowledge.Path.exists', return_value=False), \
             patch('mindspine.knowledge.Path.mkdir') as mkdir, \
             patch('mindspine.knowledge.Path.open') as opened, \
             patch('mindspine.knowledge.os.replace') as replace:
            report = import_sources('reviewed.json', 'preview-corpus')
        self.assertEqual(report['added'], 1)
        self.assertFalse(report['applied'])
        mkdir.assert_not_called()
        opened.assert_not_called()
        replace.assert_not_called()

    def test_cli_returns_structured_failure_without_writing_on_invalid_json(self):
        with patch('mindspine.knowledge.Path.read_bytes', return_value=b'{bad'), \
             patch('sys.stderr.write') as stderr:
            self.assertEqual(main(['validate', '--path', 'bad.json']), 2)
        self.assertIn('error', ''.join(call.args[0] for call in stderr.call_args_list))


if __name__ == '__main__':
    unittest.main()
