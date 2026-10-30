"""API/dispatch contract tests with synthetic adapters, never model inference."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import shutil
import threading
import time
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from mindspine.agent_tools import AgentToolService, AgentToolError, PLUGINS, TERMINAL, create_router
from mindspine.spine_agent import SpineAgentStore


@pytest.fixture
def harness():
    root = Path(__file__).resolve().parents[1] / 'temp_uploads' / ('agent-tools-test-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    store = SpineAgentStore(root / 'cases.sqlite3')
    case = store.create_case('alice', '测试病例')
    other_case = store.create_case('alice', '另一病例')
    calls, archives = [], []

    def execute(owner, case_id, run_id, step, attachment, path, output, cancelled):
        calls.append((step['plugin_id'], attachment['id'], path.read_bytes()))
        image = output / 'result.png'
        Image.new('RGB', (8, 8), 'white').save(image)
        return {'type': 'image', 'title': '合成测试结果', 'status': 'completed', 'summary': '合成输入流程验证',
                'metrics': {'value': 12.5}, 'images': [{'url': AgentToolService.artifact_url(case_id, run_id, step['id'], image.name), 'label': '测试图'}]}

    def record(owner, case_id, run_id, attachment, card):
        archives.append((owner, case_id, card))
        return 'synthetic-entry'

    service = AgentToolService(root / 'tools', store, execute,
                               lambda: {plugin['id']: {'available': True, 'reason': 'synthetic-test'} for plugin in PLUGINS},
                               max_upload_bytes=1024 * 1024, record_result=record)
    app = FastAPI()
    app.include_router(create_router(service, lambda request: request.headers.get('x-user', 'alice')))
    yield service, store, case['id'], other_case['id'], calls, archives, TestClient(app)
    service.close()
    service._pool.shutdown(wait=True)
    shutil.rmtree(root)


def image_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (16, 16), 'gray').save(buffer, format='PNG')
    return buffer.getvalue()


def uploaded(harness, modality='xray'):
    service, _, case_id, *_ = harness
    return service.upload('alice', case_id, io.BytesIO(image_bytes()), 'test.png', modality)


def wait(service, case_id, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = service.get('alice', case_id, run_id)
        if run['state'] in TERMINAL:
            return run
        time.sleep(.01)
    raise AssertionError('synthetic task did not terminate')


def test_real_dispatch_contract_stores_results_and_chat(harness):
    service, store, case_id, _, calls, archives, client = harness
    response = client.post(f'/api/cases/{case_id}/agent/attachments', data={'modality': 'xray'},
                           files={'file': ('test.png', image_bytes(), 'image/png')})
    assert response.status_code == 200
    attachment = response.json()['attachment']
    request = {'message': '分析关键点', 'attachment_ids': [attachment['id']], 'plugin_ids': ['keypoints'], 'idempotency_key': 'test-dispatch'}
    response = client.post(f'/api/cases/{case_id}/agent/tool-runs', json=request)
    assert response.status_code == 202
    result = wait(service, case_id, response.json()['run']['id'])
    assert result['state'] == 'completed'
    assert len(calls) == len(archives) == 1
    card = result['result_cards'][0]
    assert card['metrics']['value'] == 12.5
    assert card['source'] == 'model'
    assert client.get(card['images'][0]['url']).status_code == 200
    assert client.get(card['images'][0]['url'], headers={'x-user': 'bob'}).status_code == 403
    messages = store.workspace('alice', case_id)['messages']
    assert [message['role'] for message in messages] == ['user', 'assistant']
    assert messages[-1]['audit']['result_cards'] == result['result_cards']
    assert len(client.get(f'/api/cases/{case_id}/agent/tool-runs').json()['runs']) == 1


def test_idempotency_and_conflicting_payload(harness):
    service, store, case_id, _, calls, *_ = harness
    attachment = uploaded(harness)
    arguments = ('alice', case_id, '测量', [attachment['id']], ['keypoints'], 'same-key')
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: service.start(*arguments), range(4)))
    assert len({run['id'] for run in runs}) == 1
    wait(service, case_id, runs[0]['id'])
    assert len(calls) == 1
    assert len(store.workspace('alice', case_id)['messages']) == 2
    with pytest.raises(AgentToolError, match='请求标识已用于'):
        service.start('alice', case_id, '另一问题', [attachment['id']], ['keypoints'], 'same-key')


def test_attachment_scope_modality_and_plugin_whitelist(harness):
    service, store, case_id, other_case, calls, _, client = harness
    attachment = uploaded(harness)
    with pytest.raises(AgentToolError, match='不属于当前病例'):
        service.start('alice', other_case, '分析', [attachment['id']], ['keypoints'], 'cross-case')
    with pytest.raises(AgentToolError, match='无权'):
        service.start('bob', case_id, '分析', [attachment['id']], ['keypoints'], 'cross-owner')
    with pytest.raises(AgentToolError, match='没有匹配'):
        service.start('alice', case_id, '分析', [attachment['id']], ['roi'], 'wrong-modality')
    with pytest.raises(AgentToolError, match='不支持的插件'):
        service.start('alice', case_id, '分析', [attachment['id']], ['eval'], 'unknown-plugin')
    researcher = uuid.uuid4().hex
    store.invite_member('alice', case_id, researcher, 'researcher', None)
    response = client.post(f'/api/cases/{case_id}/agent/attachments', headers={'x-user': researcher},
                           data={'modality': 'xray'}, files={'file': ('test.png', image_bytes())})
    assert response.status_code == 403
    assert not calls


def test_invalid_upload_and_missing_model_do_not_execute(harness):
    service, _, case_id, _, calls, *_ = harness
    with pytest.raises(AgentToolError):
        service.upload('alice', case_id, io.BytesIO(b'not-a-png'), 'test.png', 'xray')
    with pytest.raises(AgentToolError):
        service.upload('alice', case_id, io.BytesIO(b'<svg/>'), 'test.svg', 'rgb')
    attachment = uploaded(harness)
    service.availability = lambda: {'keypoints': {'available': False, 'reason': '模型未加载'}}
    with pytest.raises(AgentToolError, match='模型未加载'):
        service.start('alice', case_id, '分析', [attachment['id']], ['keypoints'], 'no-model')
    assert not calls


def test_documents_archive_without_claiming_analysis(harness):
    service, store, case_id, _, calls, *_ = harness
    attachment = service.upload('alice', case_id, io.BytesIO(b'test notes'), 'notes.txt', 'document')
    run = service.start('alice', case_id, '保存资料', [attachment['id']], [], 'docs-only')
    result = wait(service, case_id, run['id'])
    assert result['state'] == 'completed' and not result['result_cards'] and not calls
    assert '尚未解析' in store.workspace('alice', case_id)['messages'][-1]['content']


def test_cancel_discards_pending_result_and_prevents_later_steps(harness):
    service, store, case_id, _, _, archives, _ = harness
    attachment = uploaded(harness, 'rgb')
    started = threading.Event()
    release = threading.Event()

    def slow(*args):
        started.set()
        release.wait(3)
        return {'status': 'completed', 'summary': 'should not persist', 'images': []}

    service.execute = slow
    run = service.start('alice', case_id, '分析', [attachment['id']], ['roi', 'pseudo_dr'], 'cancel-test')
    assert started.wait(2)
    try:
        response = service.cancel('alice', case_id, run['id'])
        assert response['state'] == 'cancelling'
        assert service.get('alice', case_id, run['id'])['state'] == 'cancelling'
        assert [m['role'] for m in store.workspace('alice', case_id)['messages']] == ['user']
    finally:
        release.set()
    result = wait(service, case_id, run['id'])
    assert result['state'] == 'cancelled' and not result['result_cards'] and not archives
    assert all(step['state'] == 'cancelled' for step in result['steps'])
    messages = store.workspace('alice', case_id)['messages']
    assert [m['role'] for m in messages] == ['user', 'assistant']
    assert messages[-1]['audit']['safety_status'] == 'cancelled'
    assert '取消' in messages[-1]['content']


def test_large_result_keeps_full_run_but_bounds_chat_audit(harness):
    service, store, case_id, *_ = harness
    attachment = uploaded(harness)
    payload = '测试数据' * 60_000
    service.execute = lambda *args: {
        'type': 'metrics', 'title': '完整测算', 'status': 'completed', 'summary': '完成',
        'metrics': {'value': 12.5}, 'raw_keypoint_details': payload,
    }
    run = service.start('alice', case_id, '测算', [attachment['id']], ['keypoints'], 'large-output')
    result = wait(service, case_id, run['id'])
    assert result['state'] == 'completed'
    assert result['result_cards'][0]['raw_keypoint_details'] == payload
    audit = store.workspace('alice', case_id)['messages'][-1]['audit']
    assert audit['result_cards_truncated'] is True
    assert audit['tool_run_id'] == result['id']
    assert audit['result_cards'][0]['title'] == '完整测算'
    assert len(json.dumps(audit, ensure_ascii=False).encode('utf-8')) < 96 * 1024


def test_cancelling_jobs_still_occupy_bounded_queue(harness):
    service, _, case_id, *_ = harness
    attachment = uploaded(harness)
    release = threading.Event()
    both_started = threading.Event()
    entered = []
    def slow(*args):
        entered.append(True)
        if len(entered) == 2:
            both_started.set()
        release.wait(3)
        return {'status': 'completed', 'summary': '完成'}
    service.execute = slow
    try:
        first = service.start('alice', case_id, '分析', [attachment['id']], ['keypoints'], 'slot-one')
        second = service.start('alice', case_id, '分析', [attachment['id']], ['keypoints'], 'slot-two')
        assert both_started.wait(2)
        service.cancel('alice', case_id, first['id'])
        with pytest.raises(AgentToolError, match='队列已满'):
            service.start('alice', case_id, '分析', [attachment['id']], ['keypoints'], 'slot-three')
    finally:
        release.set()
    wait(service, case_id, second['id'])


def test_restart_recovers_cancelling_run_with_chat_notice(harness):
    service, store, case_id, *_ = harness
    attachment = uploaded(harness)
    run = service.start('alice', case_id, '分析', [attachment['id']], ['keypoints'], 'interrupted')
    result = wait(service, case_id, run['id'])
    service.close()
    service._pool.shutdown(wait=True)
    result['state'] = 'cancelling'
    result['steps'][0]['state'] = 'cancelling'
    service._save(result)
    recovered = AgentToolService(service.root, store, service.execute, service.availability)
    try:
        result = recovered.get('alice', case_id, run['id'])
        assert result['state'] == 'failed'
        assert result['steps'][0]['state'] == 'failed'
        message = store.workspace('alice', case_id)['messages'][-1]
        assert message['role'] == 'assistant' and '重启' in message['content']
        assert message['audit']['tool_run_id'] == run['id']
    finally:
        recovered.close()


def test_execution_failure_is_persisted_without_internal_paths(harness):
    service, store, case_id, *_ = harness
    attachment = uploaded(harness)
    def fail(*args):
        raise RuntimeError('private-filesystem-path')
    service.execute = fail
    run = service.start('alice', case_id, '分析', [attachment['id']], ['keypoints'], 'failure')
    result = wait(service, case_id, run['id'])
    assert result['state'] == 'failed'
    assert 'private-filesystem-path' not in str(result)
    assert '执行失败' in store.workspace('alice', case_id)['messages'][-1]['content']


def test_permission_revoked_before_execution_blocks_model(harness):
    service, store, case_id, _, calls, archives, _ = harness
    attachment = uploaded(harness)
    original_access = service._access
    checks = [0]
    def revoke(owner, scope, write=False):
        checks[0] += 1
        if checks[0] > 1 and write:
            raise AgentToolError('无权访问', 403)
        return original_access(owner, scope, write)
    service._access = revoke
    run = service.start('alice', case_id, '分析', [attachment['id']], ['keypoints'], 'revoke')
    assert wait(service, case_id, run['id'])['state'] == 'failed'
    assert not calls and not archives


def test_dicom_geometry_and_no_default_millimetres(harness):
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid
    from mindspine.agent_tools_imaging import calibration_for_image
    service, _, case_id, *_ = harness
    assert calibration_for_image(Path('image.jpg'), (100, 100))['pixels_per_mm'] is None
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    image = FileDataset(None, {}, file_meta=meta, preamble=b'\0' * 128)
    image.Rows, image.Columns = 16, 16
    image.PixelSpacing = [0.5, 0.25]
    image.SamplesPerPixel, image.BitsAllocated = 1, 8
    image.BitsStored, image.HighBit, image.PixelRepresentation = 8, 7, 0
    image.PhotometricInterpretation = 'MONOCHROME2'
    image.PixelData = bytes(256)
    buffer = io.BytesIO()
    image.save_as(buffer, enforce_file_format=True)
    buffer.seek(0)
    attachment = service.upload('alice', case_id, buffer, 'example.dicom', 'xray')
    path, _ = service.attachment_file('alice', case_id, attachment['id'])
    assert path.suffix == '.dcm'
    geometry = calibration_for_image(path, (16, 16))
    assert geometry['calibrated'] is True
    assert geometry['pixels_per_mm'] == 2
    assert geometry['resample_size'] == [8, 16]
