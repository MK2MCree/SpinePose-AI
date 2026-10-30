"""Opt-in real-weight smoke, isolated from all user accounts and clinical records.

Run with the application's Python environment. Raw files never leave localhost.
"""
from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QA_ROOT = ROOT / '.analysis_tmp' / ('agent-tools-smoke-' + uuid.uuid4().hex)
os.environ['MINDSPINE_DATA_DIR'] = str(QA_ROOT)

from fastapi.testclient import TestClient
import main

logging.getLogger('httpx').setLevel(logging.WARNING)


def poll(client, case_id, run_id, timeout_seconds=150):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f'/api/cases/{case_id}/agent/tool-runs/{run_id}')
        assert response.status_code == 200, response.text[:300]
        run = response.json()['run']
        if run['state'] in {'completed', 'needs_review', 'failed', 'cancelled'}:
            return run
        time.sleep(.25)
    client.delete(f'/api/cases/{case_id}/agent/tool-runs/{run_id}')
    raise AssertionError(f'Tool run did not finish within {timeout_seconds} seconds')


def run_case(client, case_id, name, content, modality, plugins, timeout_seconds=150):
    upload = client.post(f'/api/cases/{case_id}/agent/attachments',
                         files={'file': (name, content)}, data={'modality': modality})
    assert upload.status_code == 200, upload.text[:500]
    attachment = upload.json()['attachment']
    payload = {'message': '请用已选功能分析这份测试资料。', 'attachment_ids': [attachment['id']],
               'plugin_ids': plugins, 'idempotency_key': uuid.uuid4().hex}
    start = client.post(f'/api/cases/{case_id}/agent/tool-runs', json=payload)
    assert start.status_code == 202, start.text[:500]
    run_id = start.json()['run']['id']
    repeated = client.post(f'/api/cases/{case_id}/agent/tool-runs', json=payload)
    assert repeated.json()['run']['id'] == run_id
    run = poll(client, case_id, run_id, timeout_seconds)
    assert run['state'] in {'completed', 'needs_review'}, run
    for card in run['result_cards']:
        for image in card.get('images', []):
            artifact = client.get(image['url'])
            assert artifact.status_code == 200 and len(artifact.content) > 100
        if card.get('video_url'):
            video = client.get(card['video_url'], headers={'Range': 'bytes=0-255'})
            assert video.status_code == 206 and len(video.content) == 256
        if card.get('report_url'):
            assert client.get(card['report_url']).status_code == 200
    print(json.dumps({'input': name, 'state': run['state'], 'steps': len(run['steps']),
                      'cards': len(run['result_cards']), 'errors': run['errors'],
                      'metrics': [card.get('metrics') for card in run['result_cards']],
                      'summaries': [card.get('summary') for card in run['result_cards']]}, ensure_ascii=False), flush=True)
    return run


def smoke():
    with TestClient(main.app) as client:
        password = uuid.uuid4().hex
        registration = client.post('/api/auth/register', data={'username': 'agent-qa-local', 'password': password, 'password_confirm': password})
        assert registration.status_code == 200, registration.text[:300]
        workspace = client.get('/api/cases/default/workspace').json()['workspace']
        case_id = workspace['case']['id']
        catalogue = client.get('/api/agent/plugins')
        assert catalogue.status_code == 200, catalogue.text[:300]
        plugins = {item['id']: item['available'] for item in catalogue.json()['plugins']}
        print(json.dumps({'plugins': plugins}, ensure_ascii=False), flush=True)
        assert plugins['keypoints'] and plugins['roi'] and plugins['pseudo_dr']
        dr = (ROOT / 'web/assets/case-demo/real-dr-lateral.jpg').read_bytes()
        xray = run_case(client, case_id, 'qa-lateral.jpg', dr, 'xray', ['keypoints'])
        assert 'LCI' not in xray['result_cards'][0]['metrics'], 'Uncalibrated JPEG must not claim millimeters'
        rgb = (ROOT / 'web/assets/case-demo/surface-rgb-side.png').read_bytes()
        surface = run_case(client, case_id, 'qa-surface.png', rgb, 'rgb', ['roi', 'pseudo_dr'])
        assert len(surface['result_cards']) == 2
        roi = next(card for card in surface['result_cards'] if card['plugin_id'] == 'roi')
        direct = next(card for card in surface['result_cards'] if card['plugin_id'] == 'pseudo_dr')
        if roi['status'] == 'needs_review':
            assert not roi.get('generated_image'), 'ROI rejection must not claim to generate an image'
            assert roi.get('summary'), 'Quality rejection must explain how to recover'
        # Independent tools: a failed optional ROI must never block Pix2Pix direct.
        assert direct['status'] == 'completed' and direct['generated_image']
        assert direct['inference_mode'] == 'source_direct', direct
        assert direct['model_version'] == 'best_source_generator_joint.pth', direct
        assert direct['network_input_size'] == [256, 256], direct
        assert direct['images'] and 'roi_confidence' not in direct.get('metrics', {})
        from PIL import Image
        import numpy as np
        from pydicom.dataset import FileDataset, FileMetaDataset
        from pydicom.uid import ExplicitVRLittleEndian, DigitalXRayImageStorageForPresentation, generate_uid
        image = np.asarray(Image.open(io.BytesIO(dr)).convert('L'))
        meta = FileMetaDataset()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
        meta.MediaStorageSOPInstanceUID = generate_uid()
        ds = FileDataset('', {}, file_meta=meta, preamble=b'\0' * 128)
        ds.Modality = 'DX'; ds.Rows, ds.Columns = image.shape
        ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = 'MONOCHROME2'
        ds.BitsAllocated = 8; ds.BitsStored = 8; ds.HighBit = 7; ds.PixelRepresentation = 0
        ds.PixelSpacing = [0.3, 0.3]; ds.PixelData = image.tobytes()
        output = io.BytesIO(); ds.save_as(output, enforce_file_format=True)
        dicom = run_case(client, case_id, 'qa-synthetic-spacing.dicom', output.getvalue(), 'xray', ['keypoints'])
        assert dicom['result_cards'][0]['calibration']['calibrated']
        assert dicom['result_cards'][0]['metric_units']['LCI'] == 'mm'
        run_case(client, case_id, 'qa-note.txt', b'Local QA attachment, no clinical content.', 'document', [])
        expected_runs = 4
        # Explicit opt-in to the existing public video sample; no fixture is downloaded.
        gait_sample = os.environ.get('MINDSPINE_AGENT_SMOKE_GAIT')
        if gait_sample:
            sample = Path(gait_sample)
            run_case(client, case_id, sample.name, sample.read_bytes(), 'video', ['gait'], timeout_seconds=300)
            expected_runs += 1
        persisted = client.get(f'/api/cases/{case_id}/workspace').json()['workspace']
        assert any(message.get('audit', {}).get('result_cards') for message in persisted['messages'])
        assert len(persisted['agent_tool_runs']) == expected_runs
        print(json.dumps({'result': 'passed', 'persisted_runs': expected_runs, 'data_root': str(QA_ROOT)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    smoke()
