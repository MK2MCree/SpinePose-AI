"""Offline, source-attributed Chinese clinical knowledge retrieval.

Only curated knowledge is indexed. Patient facts and model/tool outputs must be
kept in separate evidence channels by callers; this module never reads uploads.
No network requests, embeddings, patient records, or execution of source content.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Protocol, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "knowledge_sources" / "clinical_v1_20260908"
SCHEMA_VERSION = "mindspine_knowledge_v1"


def validate_sources(documents: list[dict]) -> list[dict]:
    """Validate the summary interchange schema, not clinical truth or URL reachability.

    Returns an independent copy. Every import is knowledge-only and explicitly
    unsigned; patient facts and tool measurements belong to separate stores.
    """
    if not isinstance(documents, list):
        raise ValueError("Knowledge sources must be a list")
    ids, urls = set(), set()
    required_text = ("id", "title", "publisher", "source_url", "source_date_kind",
                     "source_type", "evidence_type", "evidence_level", "summary",
                     "applicability", "limitations", "retrieval_method", "source_license", "license_note")
    required_lists = ("topics", "populations", "audiences", "usage", "keywords", "key_points", "clinical_questions")
    for number, doc in enumerate(documents, 1):
        prefix = f"Source {number}: "
        if not isinstance(doc, dict):
            raise ValueError(prefix + "expected an object")
        if doc.get("evidence_kind") != "knowledge":
            raise ValueError(prefix + "Patient facts and tool outputs cannot enter the knowledge index")
        for field in required_text:
            if not isinstance(doc.get(field), str) or not doc[field].strip():
                raise ValueError(prefix + f"missing or invalid {field}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,119}", doc["id"]):
            raise ValueError(prefix + "invalid id")
        for field in required_lists:
            if (not isinstance(doc.get(field), list) or not doc[field] or
                    any(not isinstance(value, str) or not value.strip() for value in doc[field])):
                raise ValueError(prefix + f"{field} must be a nonempty string list")
        if not set(doc["audiences"]) <= {"doctor", "family"}:
            raise ValueError(prefix + "audiences must contain doctor and/or family")
        if not set(doc["populations"]) <= {"all", "child", "adolescent", "adult", "older_adult"}:
            raise ValueError(prefix + "unsupported population")
        if not set(doc["usage"]) <= {"education", "clinical_reference", "research_reference"}:
            raise ValueError(prefix + "unsupported usage")
        url = urlsplit(doc["source_url"])
        if (url.scheme != "https" or not url.hostname or url.username or url.password or
                any(char.isspace() for char in doc["source_url"])):
            raise ValueError(prefix + "source_url must be a public HTTPS attribution URL without credentials")
        canonical_url = doc["source_url"].split("#", 1)[0].rstrip("/")
        if doc["id"] in ids or canonical_url in urls:
            raise ValueError(prefix + "duplicate source id or URL")
        ids.add(doc["id"])
        urls.add(canonical_url)
        if "source_date" not in doc:
            raise ValueError(prefix + "source_date must be explicit, with null when unknown")
        for field in ("source_date", "source_updated_at", "retrieved_at"):
            value = doc.get(field)
            if value is None and field != "retrieved_at":
                continue
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2})?(?:-\d{2})?", value):
                raise ValueError(prefix + f"invalid {field}")
            if field == "retrieved_at" and len(value) != 10:
                raise ValueError(prefix + "retrieved_at requires YYYY-MM-DD")
            try:
                date.fromisoformat(value + ("-01-01" if len(value) == 4 else "-01" if len(value) == 7 else ""))
            except ValueError as exc:
                raise ValueError(prefix + f"invalid {field}") from exc
        if doc.get("access_mode") != "summary_only" or doc.get("full_text_indexed") is not False:
            raise ValueError(prefix + "this importer accepts summary_only records with full_text_indexed=false")
        if doc.get("clinically_signed_off") is not False:
            raise ValueError(prefix + "clinically_signed_off must be false; schema validation is not clinical review")
    return deepcopy(documents)


def decode_sources(payload: bytes, *, jsonl: bool = False) -> tuple[dict, list[dict]]:
    """Read UTF-8 JSON envelope/list or JSONL (one source object per nonblank line)."""
    if len(payload) > 20 * 1024 * 1024:
        raise ValueError("Source input exceeds 20 MiB; import in smaller reviewed batches")
    content = payload.decode("utf-8-sig")
    if jsonl:
        documents = [json.loads(line) for line in content.splitlines() if line.strip()]
        envelope = {}
    else:
        data = json.loads(content)
        if isinstance(data, dict):
            if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
                raise ValueError("Unsupported knowledge schema_version")
            envelope = {key: value for key, value in data.items() if key != "sources"}
            documents = data.get("sources")
        else:
            envelope, documents = {}, data
    return envelope, validate_sources(documents)


def merge_sources(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], dict]:
    """Append new sources, skip exact repeats, reject ID/URL conflicts atomically."""
    merged = validate_sources(existing)
    incoming = validate_sources(incoming)
    by_id = {doc["id"]: doc for doc in merged}
    by_url = {doc["source_url"].split("#", 1)[0].rstrip("/"): doc for doc in merged}
    added, skipped = 0, 0
    for doc in incoming:
        url = doc["source_url"].split("#", 1)[0].rstrip("/")
        previous = by_id.get(doc["id"]) or by_url.get(url)
        if previous:
            if previous != doc:
                raise ValueError(f"Conflicting existing source: {doc['id']}; review a new corpus revision explicitly")
            skipped += 1
            continue
        merged.append(doc)
        by_id[doc["id"]], by_url[url] = doc, doc
        added += 1
    return merged, {"added": added, "skipped": skipped, "source_count": len(merged)}


def import_sources(path: str | Path, root: str | Path = DEFAULT_ROOT, *, apply: bool = False) -> dict:
    """Validate and preview by default; apply writes one atomic sources.json update.

    A sibling exclusive lock serializes imports. Readers see either the complete
    old corpus or the complete new one. No network/LLM calls are made.
    """
    source, directory = Path(path), Path(root)
    _, incoming = decode_sources(source.read_bytes(), jsonl=source.suffix.lower() == ".jsonl")
    if not incoming:
        raise ValueError("Import batch is empty")
    target = directory / "sources.json"
    if apply:
        directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".import.lock"
    lock_handle = None
    temporary = directory / f".sources-{uuid4().hex}.tmp"
    try:
        if apply:
            lock_handle = lock.open("x", encoding="utf-8")
        envelope, existing = decode_sources(target.read_bytes()) if target.exists() else ({}, [])
        merged, report = merge_sources(existing, incoming)
        report.update(applied=apply, schema_version=SCHEMA_VERSION,
                      clinically_signed_off=False, target=str(target.resolve()))
        if apply and report["added"]:
            envelope.update(schema_version=SCHEMA_VERSION, language=envelope.get("language", "zh-CN"),
                            updated_at=date.today().isoformat(), sources=merged,
                            scope=f"{len(merged)} reviewed source summaries; not exhaustive clinical coverage")
            with temporary.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(envelope, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        return report
    finally:
        if temporary.exists():
            temporary.unlink()
        if lock_handle is not None:
            lock_handle.close()
            lock.unlink()


class EmbeddingAdapter(Protocol):
    """Optional future adapter; not invoked or downloaded by this lexical index."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


STOPWORDS = (
    "请问", "请你", "帮我", "告诉我", "我想知道", "能不能", "可不可以", "有没有",
    "怎么办", "为什么", "怎么样", "什么", "怎么", "是否", "需要", "应该", "可以",
    "我们", "你们", "这个", "那个", "一些", "现在", "最近", "已经", "进行", "一个",
    "有关", "关于", "出现", "情况", "问题", "方面", "如何", "以及", "还是", "但是",
)
EN_STOPWORDS = {"the", "and", "for", "with", "what", "how", "can", "should", "is", "a", "of", "to", "in"}
GENERIC_KEYWORDS = {"成人", "儿童", "成年", "青少年", "家庭", "健康", "训练", "选择", "沟通",
                    "目标", "原因", "证据", "视频", "恢复", "功能", "工作", "环境", "安全", "心理"}
# Explicit aliases improve recall without pretending lexical matching is semantic reasoning.
ALIASES = {
    "高低肩": "肩膀不平 肩部不对称 侧弯",
    "驼背": "后凸 kyphosis",
    "核磁": "mri 磁共振",
    "x光": "xray x线 影像",
    "x-ray": "xray x线 影像",
    "拍片": "x线 影像",
    "复查": "随访 复诊",
    "走路不稳": "步态 平衡 跌倒",
    "走不稳": "步态 平衡 跌倒",
    "走路歪": "步态 不对称 侧弯",
    "腿麻": "麻木 神经 坐骨神经痛",
    "尿不出来": "排尿障碍 马尾综合征 急诊",
    "大小便失禁": "膀胱 肠道 马尾综合征 急诊",
    "尿失禁": "膀胱 马尾综合征 急诊",
    "护腰": "腰带 支具",
    "矫形器": "支具",
    "schroth": "施罗斯 psse 侧弯特异性运动",
    "康复操": "康复 运动 psse",
    "scoliosis": "脊柱侧弯",
    "sciatica": "坐骨神经痛",
    "cobb": "cobb 角度 测量",
    "tug": "tug 起立行走 计时 步态",
}


def _tokens(value: str, *, expand: bool = False) -> Counter:
    text = str(value).lower().strip()
    if expand:
        text += " " + " ".join(alias for key, alias in ALIASES.items() if key in text)
    for stopword in STOPWORDS:
        text = text.replace(stopword, " ")
    tokens = []
    for part in re.findall(r"[a-z][a-z0-9_-]*|[\u3400-\u9fff]+", text):
        if part.isascii():
            if part not in EN_STOPWORDS and len(part) > 1:
                tokens.append(part)
        else:
            for size in (2, 3, 4):
                tokens.extend(part[i:i + size] for i in range(len(part) - size + 1))
    return Counter(tokens)


def _join(document: dict) -> str:
    # Dates, publisher names, and boilerplate rights text must not create relevance.
    return " ".join([
        document["title"], document.get("summary", ""),
        " ".join(document.get("key_points", [])), " ".join(document.get("keywords", [])),
        " ".join(document.get("clinical_questions", [])), document.get("applicability", ""),
    ])


def _context_applies(document: dict, query: str) -> bool:
    """Specific-scenario sources require topic context, not a generic gait match.

    This is a retrieval filter, never a judgement about a patient's condition.
    The independent case triage still acts on recorded risk signals. A matching
    term means the user is discussing that topic, not that they have that disease.
    """
    query = query.lower()
    keywords = set(document.get('keywords') or [])
    if keywords & {'肿瘤', '癌症', '转移'}:
        if not any(term in query for term in ('肿瘤', '癌', '转移', '脊髓压迫', 'oncology',
                                               'cancer', 'metastas', 'spinal cord compression')):
            return False
    if 'postoperative' in document.get('topics', []):
        if not any(term in query for term in ('术后', '手术后', '做完手术', '做过手术', '手术恢复',
                                               '腰椎减压', '椎间盘切除', 'postoperative', 'post-op',
                                               'after surgery', 'discectomy', 'decompression')):
            return False
    return True


class KnowledgeIndex:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        source = self.root if self.root.is_file() else self.root / "sources.json"
        payload = source.read_bytes()
        _, self.documents = decode_sources(payload, jsonl=source.suffix.lower() == ".jsonl")
        self._terms = [_tokens(_join(doc)) for doc in self.documents]
        self._focus_terms = [_tokens(doc['title'] + ' ' + ' '.join(doc.get('keywords', [])))
                             for doc in self.documents]
        self._lengths = [sum(terms.values()) for terms in self._terms]
        self._average_length = sum(self._lengths) / max(len(self.documents), 1)
        self._df = Counter(term for terms in self._terms for term in terms)
        self.corpus_sha256 = hashlib.sha256(payload).hexdigest()

    def stats(self) -> dict:
        def tally(field):
            return dict(sorted(Counter(value for doc in self.documents
                                       for value in (doc.get(field, []) if isinstance(doc.get(field), list)
                                                     else [doc.get(field, "unknown")])).items()))
        return {
            "source_count": len(self.documents), "document_count": len(self.documents),
            "topics": tally("topics"), "publishers": tally("publisher"),
            "audiences": tally("audiences"), "populations": tally("populations"),
            "access_modes": tally("access_mode"), "usage": tally("usage"),
            "retrieval": "chinese_ngram_bm25_v1", "corpus_sha256": self.corpus_sha256,
            "evidence_kind": "knowledge", "clinically_signed_off": False,
        }

    def search(self, query: str, audience: str = "all", limit: int = 6, *,
               topic: str | None = None, usage: str | None = None,
               population: str | None = None) -> list[dict]:
        """Return relevant attributed summaries; [] means no supporting evidence.

        Filters are exact metadata filters, applied before ranking. Population is
        never guessed from an account role. A score is relevance, not certainty.
        """
        if not isinstance(query, str) or not query.strip() or limit <= 0:
            return []
        if len(query) > 4000:
            query = query[:4000]
        audience = {"clinician": "doctor", "professional": "doctor", "parent": "family",
                    "patient": "family", "caregiver": "family"}.get(audience, audience)
        if audience not in {"all", "doctor", "family"}:
            raise ValueError("audience must be all, doctor, or family")
        terms = _tokens(query, expand=True)
        if not terms:
            return []
        size = len(self.documents)
        ranked = []
        for doc, tf, focus, length in zip(self.documents, self._terms, self._focus_terms, self._lengths):
            if not _context_applies(doc, query):
                continue
            if audience != "all" and audience not in doc.get("audiences", []):
                continue
            if any(value and value != "all" and value not in doc.get(field, [])
                   for field, value in (("topics", topic), ("usage", usage))):
                continue
            if population and population != "all" and population not in doc.get("populations", []) and "all" not in doc.get("populations", []):
                continue
            matches = set(terms) & set(tf)
            keywords = [str(value).lower() for value in doc.get("keywords", [])]
            direct_keyword = any(len(key) >= 2 and key not in GENERIC_KEYWORDS and key in query.lower()
                                 for key in keywords)
            # One coincidental bigram in a long question is insufficient evidence.
            strong = any(len(term) >= 3 for term in matches)
            if not matches or (not direct_keyword and (len(matches) < 2 or not strong)):
                continue
            score = 0.0
            for term in matches:
                idf = math.log(1 + (size - self._df[term] + 0.5) / (self._df[term] + 0.5))
                denominator = tf[term] + 1.5 * (0.25 + 0.75 * length / max(self._average_length, 1))
                score += idf * tf[term] * 2.5 / denominator
                if term in focus:
                    score += idf * 1.5
            if direct_keyword:
                score += 2.0
            if score < 2.0:
                continue
            result = deepcopy(doc)
            points = [doc["summary"], *doc.get("key_points", [])]
            snippet = max(points, key=lambda value: len(set(_tokens(value)) & set(terms)))
            result.update(score=round(score, 4), snippet=snippet[:420], evidence_kind="knowledge",
                          citation={"id": doc["id"], "title": doc["title"], "url": doc["source_url"],
                                    "publisher": doc.get("publisher"), "source_date": doc.get("source_date"),
                                    "retrieved_at": doc.get("retrieved_at")})
            ranked.append(result)
        ranked.sort(key=lambda doc: (-doc["score"], doc["id"]))
        return ranked[:min(int(limit), 20)]


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Offline curated knowledge JSON/JSONL validation and incremental import")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate", help="Validate metadata schema; does not verify medical claims")
    check.add_argument("--path", required=True)
    ingest = commands.add_parser("import", help="Preview an import; --apply atomically saves it")
    ingest.add_argument("--path", required=True)
    ingest.add_argument("--root", default=str(DEFAULT_ROOT))
    ingest.add_argument("--apply", action="store_true")
    stats = commands.add_parser("stats")
    stats.add_argument("--root", default=str(DEFAULT_ROOT))
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            source = Path(args.path)
            _, records = decode_sources(source.read_bytes(), jsonl=source.suffix.lower() == ".jsonl")
            result = {"valid": True, "source_count": len(records), "schema_version": SCHEMA_VERSION,
                      "clinically_signed_off": False, "validation_scope": "metadata_only"}
        elif args.command == "import":
            result = import_sources(args.path, args.root, apply=args.apply)
        else:
            result = KnowledgeIndex(args.root).stats()
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (ValueError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
