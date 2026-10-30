"""Opt-in, bounded PubMed retrieval using public topics, never patient prompts.

This is an application tool, not a claimed built-in DeepSeek capability. Results
are research excerpts, not clinically reviewed guidance or private case facts.
"""
from __future__ import annotations

import json
from http.client import IncompleteRead
import os
import re
import threading
import time
from urllib import parse, request
from xml.etree import ElementTree


_BASE = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
_LOCK = threading.Lock()
_LAST_CALL = 0.0
_TOPICS = (
    (r'侧弯|侧凸|scoliosis|cobb', 'adult scoliosis', '(scoliosis[MeSH Terms] OR scoliosis[Title/Abstract])'),
    (r'腰痛|腰背|下背|low.back|lumbar', 'adult low back pain', 'low back pain[MeSH Terms]'),
    (r'颈痛|颈椎|neck.pain|cervical', 'adult neck pain', 'neck pain[MeSH Terms]'),
    (r'步态|行走|gait', 'adult gait and spine', '(gait[MeSH Terms] AND spine[MeSH Terms])'),
    (r'椎间盘|disc', 'adult intervertebral disc', 'intervertebral disc displacement[MeSH Terms]'),
    (r'脊柱|spine|spinal', 'adult spine health', 'spine[MeSH Terms]'),
)
NOTICE = '联网结果来自 PubMed，仅供一般资料解释，尚未经过本项目临床审核；片段不等于全文结论。'


def public_topic(message: str):
    """Return one fixed vocabulary entry, excluding names, numbers and history."""
    for pattern, label, term in _TOPICS:
        if re.search(pattern, message, re.I):
            return label, (f'({term}) AND (adult[MeSH Terms] OR adult[Title/Abstract]) '
                           'AND (systematic review[Publication Type] OR meta-analysis[Publication Type] '
                           'OR guideline[Publication Type]) AND hasabstract')
    return None


class _OfficialRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = parse.urlsplit(newurl)
        if target.scheme != 'https' or target.hostname != 'eutils.ncbi.nlm.nih.gov':
            raise ValueError('Unapproved retrieval redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read(endpoint, params, *, timeout=5):
    global _LAST_CALL
    if endpoint not in {'esearch.fcgi', 'efetch.fcgi'}:
        raise ValueError('Unapproved retrieval endpoint')
    contact = os.getenv('MINDSPINE_NCBI_EMAIL', '').strip()
    extra = {'email': contact} if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', contact) else {}
    url = _BASE + endpoint + '?' + parse.urlencode({'db': 'pubmed', 'tool': 'SpineInsight', **extra, **params})
    # NCBI permits at most three unauthenticated requests per second. Keep this
    # per-process gate across users; no user or case identifiers enter the URL.
    opener = request.build_opener(_OfficialRedirects())
    for attempt in range(2):
        with _LOCK:
            delay = .36 - (time.monotonic() - _LAST_CALL)
            if delay > 0:
                time.sleep(delay)
            _LAST_CALL = time.monotonic()
        try:
            with opener.open(request.Request(url, headers={'User-Agent': 'SpineInsight/1.0'}), timeout=timeout) as response:
                body = response.read(512 * 1024 + 1)
            break
        except IncompleteRead:
            # Transient chunked-transfer truncation was observed with EFetch.
            # Never parse the partial payload or retry indefinitely.
            if attempt:
                raise
    if len(body) > 512 * 1024:
        raise ValueError('Retrieval response too large')
    return body


def _plain(element, limit):
    return re.sub(r'\s+', ' ', ''.join(element.itertext()) if element is not None else '').strip()[:limit]


def search_public_evidence(message: str, *, limit=3):
    """Search official PubMed, fetch at most three short research snippets.

    No full text, author addresses, arbitrary URLs or user-provided search query
    is downloaded. Errors deliberately expose no raw request or private input.
    """
    topic = public_topic(message)
    if topic is None:
        return {'status': 'unsupported_topic', 'evidence': [], 'notice': '当前联网检索仅支持脊柱相关公共医学主题。'}
    label, query = topic
    result = {'status': 'no_results', 'topic': label, 'evidence': [], 'notice': NOTICE}
    try:
        count = min(3, max(1, int(limit)))
        found = json.loads(_read('esearch.fcgi', {'term': query, 'retmode': 'json', 'retmax': count, 'sort': 'relevance'}))
        ids = [str(value) for value in found.get('esearchresult', {}).get('idlist', [])
               if re.fullmatch(r'[0-9]{1,12}', str(value))][:count]
        if not ids:
            return result
        body = _read('efetch.fcgi', {'id': ','.join(ids), 'retmode': 'xml'})
        # PubMed XML normally carries a harmless external DTD declaration;
        # ElementTree does not resolve that DTD. Explicit entities are rejected.
        if b'<!ENTITY' in body:
            raise ValueError('Unexpected XML entity')
        tree = ElementTree.fromstring(body)
        for article in tree.findall('./PubmedArticle')[:count]:
            pmid = _plain(article.find('./MedlineCitation/PMID'), 12)
            if pmid not in ids:
                continue
            citation = article.find('./MedlineCitation/Article')
            title = _plain(citation.find('ArticleTitle'), 300) if citation is not None else ''
            parts = citation.findall('./Abstract/AbstractText') if citation is not None else []
            section = next((part for part in parts if part.get('NlmCategory') == 'CONCLUSIONS'
                            or 'CONCLUSION' in part.get('Label', '').upper()), parts[0] if parts else None)
            abstract = _plain(section, 360)
            if not title or not abstract:
                continue  # A title alone cannot support a medical statement.
            date = citation.find('./Journal/JournalIssue/PubDate')
            year_match = re.search(r'(?:19|20)\d{2}', _plain(date.find('Year') if date is not None else None, 4)
                                   or _plain(date, 80))
            result['evidence'].append({'id': 'pubmed_' + pmid, 'title': title,
                'source_url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/',
                'excerpt': abstract, 'summary': abstract, 'year': year_match.group(0) if year_match else None,
                'review_status': 'online_unreviewed', 'source_kind': 'pubmed_excerpt',
                'abstract_section': section.get('Label') or section.get('NlmCategory') or 'abstract_excerpt',
                'limitations': NOTICE, 'copyright_notice_url': 'https://www.ncbi.nlm.nih.gov/About/disclaimer.html'})
        if result['evidence']:
            result['status'] = 'completed'
    except Exception:
        result['status'] = 'unavailable'
        result['evidence'] = []
        result['notice'] = '本次联网检索未成功，没有使用联网来源。'
    return result
