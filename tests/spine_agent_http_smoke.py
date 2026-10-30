"""Live HTTP smoke test for the controlled Spine Health Agent flow.

Run this only against an isolated local data directory, for example:
  $env:MINDSPINE_TEST_BASE_URL='http://127.0.0.1:8765'
  & 'C:/Users/MK2SG/miniconda3/envs/PythonProject4/python.exe' tests/spine_agent_http_smoke.py
"""

from __future__ import annotations

import os
import time

import requests


BASE_URL = os.getenv("MINDSPINE_TEST_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def expect(response: requests.Response, status: int = 200) -> dict:
    assert response.status_code == status, (response.status_code, response.text[:500])
    payload = response.json()
    assert payload["code"] == status, payload
    return payload


def main() -> int:
    session = requests.Session()
    username = f"AgentSmoke{os.getpid()}{int(time.time()) % 100000}"
    expect(
        session.post(
            f"{BASE_URL}/api/auth/register",
            data={"username": username, "password": "correct horse battery staple", "password_confirm": "correct horse battery staple"},
            timeout=15,
        )
    )
    workspace = expect(session.get(f"{BASE_URL}/api/cases/default/workspace", timeout=15))["workspace"]
    case_id = workspace["case"]["id"]
    expect(session.put(f"{BASE_URL}/api/cases/{case_id}/consent", json={"external_ai_allowed": False}, timeout=15))
    questionnaire = expect(
        session.post(
            f"{BASE_URL}/api/cases/{case_id}/questionnaire",
            json={"answers": {"consent_confirmed": True, "pain_score": 2, "activity_limit": 1, "red_flags": []}},
            timeout=15,
        )
    )["questionnaire"]
    assert questionnaire["triage"]["risk_level"] == "routine"

    reply = expect(
        session.post(f"{BASE_URL}/api/cases/{case_id}/agent/messages", json={"message": "我想做视频步态评估"}, timeout=15)
    )["agent"]
    assert not reply['proposed_actions']
    assert any(card['type']=='navigation' and 'gait-workspace.html' in card['url'] for card in reply['result_cards'])
    # Without a uploaded video there must be no placeholder inference job.
    assert not expect(session.get(f'{BASE_URL}/api/gait/jobs?case_id={case_id}',timeout=15))['jobs']
    review = expect(session.post(f'{BASE_URL}/api/cases/{case_id}/agent/messages',json={'message':'请安排专业复核'},timeout=15))['agent']
    action = review['proposed_actions'][0]
    assert action['kind']=='request_clinical_review'
    confirmed = expect(
        session.post(
            f"{BASE_URL}/api/agent-actions/{action['id']}/confirm",
            json={"case_id": case_id, "confirmation_token": action["confirmation_token"]},
            timeout=15,
        )
    )["action"]
    assert confirmed['result']['review_task']['id']

    consent = expect(
        session.put(f"{BASE_URL}/api/cases/{case_id}/consent", json={"external_ai_allowed": False}, timeout=15)
    )["consent"]
    assert consent["external_ai_allowed"] is False
    protected = requests.get(f"{BASE_URL}/api/cases/default/workspace", timeout=15)
    assert protected.status_code == 401, protected.text
    print("Spine Health Agent HTTP smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
