"""Opt-in workflow HTTP smoke using a fresh local database and zero AI calls."""
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['MINDSPINE_DATA_DIR'] = str(ROOT / '.analysis_tmp' / ('workflow-smoke-' + uuid.uuid4().hex))

from fastapi.testclient import TestClient
import main


def run():
    with TestClient(main.app) as client:
        password = uuid.uuid4().hex
        response = client.post('/api/auth/register', data={'username': 'workflow-test', 'password': password, 'password_confirm': password})
        assert response.status_code == 200, response.text
        ws = client.get('/api/cases/default/workspace').json()['workspace']
        case_id = ws['case']['id']
        client.put(f'/api/cases/{case_id}/consent', json={'external_ai_allowed': False})
        message = client.post(f'/api/cases/{case_id}/agent/messages', json={'message': '给我做一份调查问卷，评估一下脊柱健康情况'})
        assert message.status_code == 200, message.text
        assert any(c['type'] == 'questionnaire' for c in message.json()['agent']['result_cards'])
        schema = client.get('/api/questionnaire/schema').json()['schema']
        answers = {}
        for section in schema['sections']:
            for field in section['fields']:
                if field['type'] == 'text':
                    value = '无' if field['required'] else ''
                elif field['type'] == 'score': value = 2
                elif field['type'] == 'consent': value = True
                elif field['type'] == 'multi': value = [field['options'][0]['value']]
                else:
                    values = [o['value'] for o in field['options']]
                    value = 'no' if 'no' in values else 'none' if 'none' in values else values[0]
                if field['id'] == 'adult_confirmed': value = 'yes'
                parts = field['id'].split('.')
                if len(parts) == 2: answers.setdefault(parts[0], {})[parts[1]] = value
                else: answers[parts[0]] = value
        saved = client.post(f'/api/cases/{case_id}/questionnaire', json={'answers': answers, 'protocol_version': schema['version']})
        assert saved.status_code == 200, saved.text
        q = saved.json()['questionnaire']
        assert q['triage']['data_complete'] and q['triage']['analysis']
        refreshed = client.get(f'/api/cases/{case_id}/workspace').json()['workspace']
        assert refreshed['questionnaire']['id'] == q['id']
        assert refreshed['messages'][-1]['audit']['questionnaire_id'] == q['id']
        message = client.post(f'/api/cases/{case_id}/agent/messages', json={'message': '生成一份健康评估报告，PDF和Word都要'})
        assert message.status_code == 200, message.text
        card = next(c for c in message.json()['agent']['result_cards'] if c['type'] == 'report_export')
        options = {**card['options'], 'format': 'both'}
        report = client.post(f'/api/cases/{case_id}/reports', json={'confirmation': True, 'request_id': str(uuid.uuid4()), 'options': options})
        assert report.status_code == 200, report.text
        for file in report.json()['report']['files']:
            result = client.get(file['url'])
            assert result.status_code == 200 and len(result.content) > 1000
            assert result.content.startswith(b'%PDF' if file['url'].endswith('.pdf') else b'PK')
        assert client.get('/health-agent-workflows.js').status_code == 200
        assert client.get('/spine-questionnaire.js').status_code == 200
        print('PASS: questionnaire chat -> complete intake -> persisted analysis -> customized PDF + DOCX downloads')
        print('Isolated test data:', os.environ['MINDSPINE_DATA_DIR'])


if __name__ == '__main__':
    run()
