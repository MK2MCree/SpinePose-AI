import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mindspine.report_api import create_router
from mindspine.spine_agent import SpineAgentStore


@pytest.fixture
def tmp_path():
    root = Path(__file__).resolve().parents[1] / '.analysis_tmp'
    root.mkdir(exist_ok=True)
    folder = root / ('report-api-' + uuid.uuid4().hex)
    folder.mkdir()
    return folder


def setup_api(tmp_path, monkeypatch):
    from mindspine import agent_reports
    store = SpineAgentStore(tmp_path / 'cases.db')
    case = store.create_case('owner', '测试病例')
    app = FastAPI()
    counts = []
    monkeypatch.setattr(agent_reports, 'build_agent_report', lambda w, p, o, f: counts.append(f) or (b'%PDF-test' if f == 'pdf' else b'PK-docx'))
    def user(request: Request):
        return SimpleNamespace(user_id=request.headers.get('x-test-user', 'owner'))
    app.include_router(create_router(root=tmp_path / 'reports', store=store, current_user=user,
        workspace_provider=lambda user, case_id: store.workspace(user.user_id, case_id),
        profile_provider=lambda case: {'display_name': '测试用户'}))
    return TestClient(app), store, case['id'], counts


def test_confirm_snapshot_download_idempotency_and_scope(tmp_path, monkeypatch):
    client, store, case_id, counts = setup_api(tmp_path, monkeypatch)
    url = f'/api/cases/{case_id}/reports'
    payload = {'options': {'format': 'both'}, 'confirmation': False, 'request_id': str(uuid.uuid4())}
    assert client.post(url, json=payload).status_code == 400
    payload['confirmation'] = True
    result = client.post(url, json=payload)
    assert result.status_code == 200, result.text
    report = result.json()['report']
    assert len(report['files']) == 2
    for file in report['files']:
        download = client.get(file['url'])
        assert download.status_code == 200
        assert download.headers['cache-control'] == 'private, no-store'
        assert 'attachment' in download.headers['content-disposition']
        assert client.get(file['url'], headers={'x-test-user': 'stranger'}).status_code == 403
    assert client.post(url, json=payload).json()['report'] == report
    assert counts == ['pdf', 'docx']
    assert len(store.workspace('owner', case_id)['messages']) == 1
    payload['options']['title'] = '不允许覆盖'
    assert client.post(url, json=payload).status_code == 400


def test_read_only_collaborator_cannot_export_or_download(tmp_path, monkeypatch):
    client, store, case_id, _ = setup_api(tmp_path, monkeypatch)
    reader_id = uuid.uuid4().hex
    store.invite_member('owner', case_id, reader_id, 'researcher', None)
    url = f'/api/cases/{case_id}/reports'
    data = {'options': {'format': 'pdf'}, 'confirmation': True, 'request_id': str(uuid.uuid4())}
    result = client.post(url, json=data).json()['report']
    assert client.post(url, json=data, headers={'x-test-user': reader_id}).status_code == 403
    assert client.get(result['files'][0]['url'], headers={'x-test-user': reader_id}).status_code == 403


def test_bad_id_and_failed_second_format_not_published(tmp_path, monkeypatch):
    from mindspine import agent_reports
    client, store, case_id, _ = setup_api(tmp_path, monkeypatch)
    url = f'/api/cases/{case_id}/reports'
    data = {'options': {'format': 'both'}, 'confirmation': True, 'request_id': '../escape'}
    assert client.post(url, json=data).status_code == 400
    data['request_id'] = str(uuid.uuid4())
    def build(w, p, o, fmt):
        if fmt == 'docx':
            raise ImportError('missing dependency')
        return b'%PDF-test'
    monkeypatch.setattr(agent_reports, 'build_agent_report', build)
    assert client.post(url, json=data).status_code == 503
    assert not list((tmp_path / 'reports').rglob('manifest.json'))
    assert not store.workspace('owner', case_id)['messages']
