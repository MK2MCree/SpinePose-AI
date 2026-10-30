"""Read-only, bounded history of the caller's own examinations.

This module lists recorded values; it does not infer clinical improvement,
patient identity, or equivalence between tools, views, scales, or model versions.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import math
import re


METRICS = {'LL', 'SS', 'SSA', 'LCA', 'HARRISON_A', 'LCI', 'cobb_angle', 'atr_angle',
           'pain_score', 'activity_limit'}


def requests_history(message: str) -> bool:
    """Explicit history intent, excluding negation and general explanations."""
    requested = False
    for clause in re.split(r'[，,。！？!?；;\n]', message):
        if not re.search(r'历次|历年|趋势|历史(?:记录|检查|结果|趋势|对比)|(?:检查|结果|变化|疼痛|步态|侧弯|我的).*趋势|'
                         r'趋势.*(?:检查|结果|变化|疼痛|步态|侧弯)|随访(?:对比|比较)|'
                         r'(?:上次|以前|之前|过去).*(?:比|变化|改善|变差)|(?:对比|比较).*(?:上次|历次|历史|之前)', clause):
            continue
        requested = not bool(re.search(r'不要|不用|无需|不想|暂不|别|不查看|不比较|不对比|只想了解|是什么|什么是|什么意思|解释一下.*概念', clause))
    return requested


def _number(value, minimum=-10000, maximum=10000):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return round(value, 6) if math.isfinite(value) and minimum <= value <= maximum else None
    except (TypeError, ValueError):
        return None


def _date(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')
        if isinstance(value, str):
            if len(value) == 10:
                return date.fromisoformat(value).isoformat()
            return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _version(value):
    # Machine metadata only, excluding paths/free-text notes and arbitrary blobs.
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.+ -]{1,96}', value):
        return value
    return None


def _scores(report):
    if (report.get('research_demo') or report.get('status') not in {None, 'completed'} or
            (report.get('quality') or {}).get('passed') is not True):
        return {}
    raw = (report.get('analysis') or {}).get('model_scores') or {}
    result = {key: _number(raw.get(key), 0, 1) for key in ('negative', 'neutral', 'positive')}
    if any(value is None for value in result.values()) or abs(sum(result.values()) - 1) > .001:
        return {}
    return result


def _quality(report):
    raw = report.get('quality') or {}
    result = {'passed': raw.get('passed') if isinstance(raw.get('passed'), bool) else None}
    for key in ('valid_frames', 'sampled_frames', 'usable_coverage'):
        value = _number(raw.get(key), 0)
        if value is not None:
            result[key] = value
    result['issue_codes'] = ([code for item in raw.get('issues', []) if isinstance(item, dict)
                              if (code := _version(item.get('code')))] +
                             [code for value in raw.get('issue_codes', []) if (code := _version(value))])[:12]
    return result


class ExamHistoryProvider:
    def __init__(self, store, gait, personal_store=None, *, max_cases=20, max_records=100):
        self.store, self.gait, self.personal_store = store, gait, personal_store
        self.max_cases = max(1, min(int(max_cases), 50))
        self.max_records = max(1, min(int(max_records), 200))

    def __call__(self, user_id, current_case_id):
        current = self.store.get_case(user_id, current_case_id)
        if current.get('owner_user_id') != user_id:
            return self._result([], [], False, False)
        candidates = [case for case in self.store.list_cases(user_id)
                      if case.get('owner_user_id') == user_id]
        # List order is updated_at; examination order must use recorded timestamps.
        candidates.sort(key=lambda item: item.get('created_at') or 0, reverse=True)
        truncated = len(candidates) > self.max_cases
        cases, failures, records = {}, [], []
        for case in candidates[:self.max_cases]:
            case_id = case.get('id')
            try:
                checked = self.store.get_case(user_id, case_id)
                if checked.get('owner_user_id') != user_id:
                    continue
                consent = self.store.get_consent(user_id, case_id)
                cases[case_id] = consent.get('external_ai_allowed') is True
            except Exception:
                failures.append('case_access')
                continue
            try:
                questionnaire = self.store.latest_questionnaire(user_id, case_id) or {}
                answers = questionnaire.get('answers') or {}
                values = {key: value for key in ('pain_score', 'activity_limit')
                          if (value := _number(answers.get(key), 0, 10)) is not None}
                if values:
                    records.append({'case_id': case_id, 'record_id': questionnaire.get('id'),
                                    'kind': 'questionnaire', 'date': _date(questionnaire.get('created_at')),
                                    'date_basis': 'questionnaire_recorded_at', 'measurements': values,
                                    'protocol_version': _version(questionnaire.get('protocol_version')),
                                    'evidence_kind': 'patient_fact'})
            except Exception:
                failures.append('questionnaire')
            try:
                jobs = self.gait.list(user_id, case_id, limit=50)
                archived = [job for job in jobs if job.get('case_id') == case_id
                            and job.get('status') in {'completed', 'rejected'}
                            and (job.get('review') or {}).get('archived') is True]
                archived.sort(key=lambda job: ((job.get('review') or {}).get('selected') is True,
                                                job.get('created_at') or 0), reverse=True)
                # One adopted/latest archive per examination; reruns are not separate visits.
                if archived:
                    job = archived[0]
                    report = self.gait.report(user_id, job['id'])
                    if report.get('case_id') == case_id and report.get('job_id') == job['id']:
                        model = report.get('model') or {}
                        records.append({'case_id': case_id, 'record_id': job['id'], 'kind': 'gait',
                                        'date': _date(job.get('created_at')), 'date_basis': 'job_created_at',
                                        'model_scores': _scores(report) if job['status'] == 'completed' else {},
                                        'engine_version': _version(report.get('report_view_version') or
                                                                   report.get('engine_version') or job.get('engine_version')),
                                        'model_version': _version(model.get('model_name') or model.get('name')),
                                        'checkpoint_sha256': _version(model.get('checkpoint_sha256')),
                                        'quality': _quality(report), 'archived': True,
                                        'selected': (job.get('review') or {}).get('selected') is True,
                                        'research_demo': bool(report.get('research_demo')),
                                        'evidence_kind': 'tool_result'})
            except Exception:
                failures.append('gait')
        if self.personal_store is not None:
            try:
                entries = self.personal_store.list_entries(user_id, limit=500)
                truncated = truncated or len(entries) >= 500
                legacy_id = next((case_id for case_id in cases if self.store.is_legacy_case(user_id, case_id)), None)
                for entry in entries:
                    # Public list output omits user_id; recheck its owned lookup.
                    entry = self.personal_store.get_entry(user_id, entry.get('id'))
                    if not entry:
                        continue
                    case_id = entry.get('case_id') or legacy_id
                    if case_id not in cases or entry.get('status') not in {'completed', 'manual'}:
                        continue
                    if entry.get('kind') not in {'dr', 'rgb', 'clinical', 'checkin'}:
                        continue
                    payload = entry.get('payload') or {}
                    angles = payload.get('angles') if isinstance(payload.get('angles'), dict) else {}
                    values = {key: value for key in sorted(METRICS)
                              if (value := _number(angles.get(key, payload.get(key)))) is not None}
                    if values:
                        records.append({'case_id': case_id, 'record_id': entry.get('id'),
                                        'kind': entry['kind'], 'date': _date(entry.get('recorded_at')),
                                        'date_basis': 'entry_recorded_at', 'measurements': values,
                                        'association': 'linked' if entry.get('case_id') else 'legacy_unassigned',
                                        'protocol_version': _version(payload.get('protocol_version')),
                                        'evidence_kind': 'patient_fact' if entry['kind'] in {'clinical', 'checkin'} else 'tool_result'})
            except Exception:
                failures.append('personal_entries')
        records.sort(key=lambda item: (item.get('date') or '', item['kind']), reverse=True)
        truncated = truncated or len(records) > self.max_records
        records = records[:self.max_records]
        included_cases = {row['case_id'] for row in records}
        allow_external = bool(records) and all(cases.get(case_id, False) for case_id in included_cases)
        return self._result(records, failures, truncated, allow_external)

    @staticmethod
    def _result(records, failures, truncated, allow_external):
        return {'records': records, 'record_count': len(records),
                'case_count': len({record['case_id'] for record in records}), 'truncated': truncated,
                'external_ai_allowed': allow_external, 'unavailable_sources': sorted(set(failures)),
                'scope': 'owned_examinations', 'clinical_trend_inferred': False,
                'notice': '按检查日期分别列示；同一账号的检查不自动确认同一受检者。不同模态、版本及拍摄条件不合并评分；旧未关联记录单独标注。'}


def history_context(history):
    """Whitelist for external explanation, with no case/entry IDs, notes or paths."""
    safe = []
    for row in (history.get('records') or [])[:100]:
        item = {key: row[key] for key in ('kind', 'date', 'date_basis', 'engine_version',
                'model_version', 'protocol_version', 'archived', 'selected', 'research_demo',
                'association', 'evidence_kind') if key in row}
        item['measurements'] = {key: value for key in METRICS
                                if (value := _number((row.get('measurements') or {}).get(key))) is not None}
        item['model_scores'] = {key: value for key in ('negative', 'neutral', 'positive')
                                if (value := _number((row.get('model_scores') or {}).get(key), 0, 1)) is not None}
        item['quality'] = _quality({'quality': row.get('quality') or {}})
        safe.append(item)
    return {'records': safe, 'truncated': bool(history.get('truncated')),
            'clinical_trend_inferred': False, 'different_modalities_must_not_be_combined': True}


def describe_history(history):
    rows = history.get('records') or []
    if not rows:
        return '还没有可用于历次对比的已记录数值或归档步态结果，目前无法判断变化趋势。'
    lines = [f"找到 {len(rows)} 条记录，来自 {history.get('case_count', 0)} 次自有检查。"]
    labels = {'questionnaire': '问卷', 'gait': '归档步态', 'dr': 'DR影像', 'rgb': '体表RGB',
              'clinical': '人工记录', 'checkin': '状态打卡'}
    metrics = {'pain_score': '疼痛', 'activity_limit': '活动受限', 'cobb_angle': 'Cobb', 'atr_angle': 'ATR'}
    for row in rows[:6]:
        values = '、'.join(f"{metrics.get(key, key)} {value:g}" for key, value in (row.get('measurements') or {}).items())
        if row.get('kind') == 'gait':
            scores = row.get('model_scores') or {}
            values = '、'.join(f"{label} {scores[key]:.3f}" for key, label in
                              (('negative', '阴性类'), ('neutral', '中间类'), ('positive', '阳性类')) if key in scores)
            if not values:
                values = '没有可用于对比的有效模型分数'
            values += '；质控' + ('通过' if (row.get('quality') or {}).get('passed') is True else '未通过或未知')
        lines.append(f"{(row.get('date') or '日期未记录')[:10]} · {labels.get(row['kind'], row['kind'])}：{values}")
    lines.append('不同模态、模型版本和拍摄条件不能合并为一个趋势分数；模型分数变化不等于病情改善。')
    if history.get('truncated'):
        lines.append('当前为有数量上限的记录摘要。')
    return '\n'.join(lines)
