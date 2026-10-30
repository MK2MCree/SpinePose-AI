"""Opt-in real inference check, against an isolated local server and public sample."""
from pathlib import Path
import json
import os
import time
import uuid
import requests


def main():
    base=os.getenv('MINDSPINE_TEST_BASE_URL','http://127.0.0.1:8766')
    session=requests.Session()
    username='Integration'+uuid.uuid4().hex[:10]
    password=uuid.uuid4().hex
    response=session.post(base+'/api/auth/register',data={'username':username,'password':password,'password_confirm':password},timeout=20)
    response.raise_for_status()
    case=session.post(base+'/api/cases',json={'title':'公开样例 · 完整流程验证','care_scope':'spine_exam'},timeout=20).json()['case']
    case_id=case['id']
    response=session.post(base+f'/api/cases/{case_id}/questionnaire',json={'answers':{'consent_confirmed':True,'pain_score':1,'activity_limit':1,'red_flags':[],
        'symptom_location':['背部'],'duration':'流程测试','function_impacts':[],'goals':'验证报告、任务及归档流程'},'protocol_version':'spine-intake-v2'},timeout=20)
    response.raise_for_status()
    # Public sample has no patient identity; no external provider is used here.
    session.put(base+f'/api/cases/{case_id}/consent',json={'external_ai_allowed':False},timeout=20).raise_for_status()
    sample=Path.home()/'Documents/ChatGPT/脊柱步态识别/ScoNetVideoApp/samples/opengait_gallery_original.mp4'
    with sample.open('rb') as stream:
        response=session.post(base+'/api/gait/jobs',data={'case_id':case_id,'options_json':json.dumps({'target_track_id':2,'research_demo':True})},
            files={'file':(sample.name,stream,'video/mp4')},timeout=30)
    assert response.status_code==202,response.text
    job=response.json()['job']; started=time.monotonic(); previous=None
    while job['status'] in {'uploading','queued','running'}:
        if (job['status'],job['stage'])!=previous:
            previous=(job['status'],job['stage']); print(json.dumps({'status':job['status'],'stage':job['stage'],'progress':job['progress']}),flush=True)
        assert time.monotonic()-started<300,'inference timeout'
        time.sleep(1)
        job=session.get(base+'/api/gait/jobs/'+job['id'],timeout=15).json()['job']
    assert job['status']=='completed',job
    report=session.get(base+f"/api/gait/jobs/{job['id']}/report",timeout=20).json()['report']
    assert report['report_view_version']=='2.0.1'
    assert report['prediction'] is None
    assert report['quality']['passed']
    private_url=base+f"/api/gait/jobs/{job['id']}/report"
    assert requests.get(private_url,timeout=10).status_code==401
    for name in ('input_preview.mp4','overlay.mp4','masks.mp4'):
        r=session.get(base+f"/api/gait/jobs/{job['id']}/files/{name}",headers={'Range':'bytes=0-255'},timeout=20)
        assert r.status_code==206,(name,r.status_code)
    review=session.post(base+f"/api/gait/jobs/{job['id']}/review",json={'notes':'公开样例：已核对人物、轮廓与播放。','archive':True},timeout=20)
    review.raise_for_status(); assert review.json()['job']['review']['selected']
    pdf=session.get(base+f'/api/cases/{case_id}/report.pdf',timeout=30); pdf.raise_for_status(); assert pdf.content.startswith(b'%PDF')
    output=Path('output/pdf'); output.mkdir(parents=True,exist_ok=True); (output/'SpineInsight_综合检查报告_示例.pdf').write_bytes(pdf.content)
    reply=session.post(base+f'/api/cases/{case_id}/agent/messages',json={'message':'请整理本次步态结果和综合报告'},timeout=30)
    reply.raise_for_status(); agent=reply.json()['agent']; assert any(c['type']=='gait_result' for c in agent['result_cards'])
    evidence=session.get(base+'/api/knowledge/search',params={'q':'脊柱侧弯的检查和随访','limit':5},timeout=20).json()['evidence']
    assert evidence
    cookie=session.cookies.get('mindspine_auth')
    session.post(base+'/api/auth/logout',timeout=20).raise_for_status()
    assert requests.get(base+'/api/auth/me',cookies={'mindspine_auth':cookie},timeout=10).status_code==401
    result={'case_id':case_id,'job_id':job['id'],'status':job['status'],'elapsed_seconds':round(time.monotonic()-started,2),
        'valid_frames':report['quality']['valid_frames'],'report_view_version':report['report_view_version'],
        'checks':['upload','GPU pipeline','V2.0.1 report','Range videos','private ownership','review/archive','PDF','Agent reads real result','RAG retrieval','logout revocation']}
    path=Path('.analysis_tmp/integration_v201_result.json'); path.parent.mkdir(exist_ok=True); path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__': main()
