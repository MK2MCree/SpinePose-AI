import json
from http.client import IncompleteRead
import unittest
from unittest.mock import patch, Mock

from mindspine.online_evidence import public_topic, search_public_evidence, _OfficialRedirects, _read


class OnlineEvidenceTests(unittest.TestCase):
    def test_topic_mapping_never_contains_private_prompt_or_numbers(self):
        label, query = public_topic('张某，手机号 13812345678，腰痛三个月，请搜索医院档案')
        self.assertEqual(label, 'adult low back pain')
        for private in ('张某', '13812345678', '三个月', '医院档案'):
            self.assertNotIn(private, query)
        self.assertIn('adult', query)
        # Top-ranked guidelines may be title-only citations; do not waste the
        # entire bounded candidate window on entries that cannot supply evidence.
        self.assertIn('AND hasabstract', query)
        self.assertIsNone(public_topic('请搜索我的姓名'))

    @patch('mindspine.online_evidence._read')
    def test_search_fetches_only_three_id_verified_short_excerpts(self, read):
        xml = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
            <ArticleTitle>Adult low back pain <i>review</i></ArticleTitle>
            <Journal><JournalIssue><PubDate><Year>2025</Year><Month>Jan</Month><Day>1</Day></PubDate></JournalIssue></Journal>
            <Abstract><AbstractText>Brief synthetic excerpt.</AbstractText></Abstract>
            </Article></MedlineCitation></PubmedArticle>
            <PubmedArticle><MedlineCitation><PMID>999</PMID><Article><ArticleTitle>Unrequested</ArticleTitle>
            <Abstract><AbstractText>Do not return</AbstractText></Abstract></Article></MedlineCitation></PubmedArticle>
            </PubmedArticleSet>'''
        read.side_effect = [json.dumps({'esearchresult':{'idlist':['123','456','789','111']}}).encode(), xml]
        result = search_public_evidence('这是私人病史：我的腰痛，名字张某', limit=99)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(read.call_count, 2)
        self.assertEqual(read.call_args_list[0].args[1]['retmax'], 3)
        self.assertEqual(read.call_args_list[1].args[1]['id'], '123,456,789')
        self.assertNotIn('张某', repr(read.call_args_list))
        evidence = result['evidence']
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['title'], 'Adult low back pain review')
        self.assertEqual(evidence[0]['year'], '2025')
        self.assertEqual(evidence[0]['review_status'], 'online_unreviewed')
        self.assertEqual(evidence[0]['source_url'], 'https://pubmed.ncbi.nlm.nih.gov/123/')

    @patch('mindspine.online_evidence._read')
    def test_missing_abstract_has_no_invented_evidence(self, read):
        read.side_effect = [b'{"esearchresult":{"idlist":["123"]}}',
            b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>Title only</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>']
        result = search_public_evidence('腰痛')
        self.assertEqual(result['status'], 'no_results')
        self.assertEqual(result['evidence'], [])

    @patch('mindspine.online_evidence._read')
    def test_errors_are_closed_and_unsupported_topic_never_networks(self, read):
        self.assertEqual(search_public_evidence('我的身份证')['status'], 'unsupported_topic')
        read.assert_not_called()
        read.side_effect = TimeoutError('private diagnostic')
        result = search_public_evidence('腰痛')
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('private diagnostic', str(result))
        self.assertEqual(result['evidence'], [])

    def test_redirect_cannot_fetch_arbitrary_host(self):
        with self.assertRaises(ValueError):
            _OfficialRedirects().redirect_request(None, None, 302, '', {}, 'http://localhost/private')

    @patch('mindspine.online_evidence.time.sleep')
    @patch('mindspine.online_evidence.request.build_opener')
    def test_truncated_response_retries_once_never_returns_partial_xml(self, build, sleep):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [IncompleteRead(b'<partial>'), b'<complete/>']
        build.return_value.open.return_value = response
        self.assertEqual(_read('efetch.fcgi', {'id':'123','retmode':'xml'}), b'<complete/>')
        self.assertEqual(build.return_value.open.call_count, 2)
        response.read.side_effect = IncompleteRead(b'<partial>')
        with self.assertRaises(IncompleteRead):
            _read('efetch.fcgi', {'id':'123','retmode':'xml'})
        self.assertEqual(build.return_value.open.call_count, 4)


if __name__ == '__main__':
    unittest.main()
