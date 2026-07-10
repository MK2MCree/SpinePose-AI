from __future__ import annotations

import glob
import os
import time
from pathlib import Path

import requests


BASE_URL = os.getenv("MINDSPINE_TEST_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]


def expect_json(response: requests.Response, expected_status: int = 200) -> dict:
    assert response.status_code == expected_status, (response.status_code, response.text[:500])
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def upload(session: requests.Session, path: Path, mode: str) -> dict:
    with path.open("rb") as handle:
        response = session.post(
            f"{BASE_URL}/api/upload",
            files={"file": (path.name, handle, "application/octet-stream")},
            data={"mode": mode},
            timeout=120,
        )
    payload = expect_json(response)
    assert payload["code"] == 200
    return payload


def poll_job(session: requests.Session, job_id: str) -> dict:
    for _ in range(120):
        payload = expect_json(session.get(f"{BASE_URL}/api/batch_jobs/{job_id}", timeout=15))
        if payload["job"]["status"] in {"completed", "failed"}:
            return payload["job"]
        time.sleep(0.25)
    raise AssertionError(f"batch job timed out: {job_id}")


def first(patterns: list[str]) -> Path:
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(glob.glob(str(ROOT / pattern), recursive=True))
    assert matches, f"missing local regression asset: {patterns}"
    return Path(sorted(matches, key=lambda value: Path(value).stat().st_size)[0])


def main() -> int:
    dicom = first(["temp_uploads/*.dcm", "temp_uploads/*.DCM"])
    rgb = first(["temp_uploads/*.jpg", "temp_uploads/*.png"])
    source = first(["temp_uploads/pseudoct_inputs/**/*.png"])

    owner = requests.Session()
    outsider = requests.Session()

    expect_json(owner.get(f"{BASE_URL}/health/live", timeout=10))
    ready = expect_json(owner.get(f"{BASE_URL}/health/ready", timeout=30))
    assert ready["models"]["xray"] and ready["models"]["rgb"]

    xray_case = upload(owner, dicom, "xray")
    expect_json(
        outsider.post(
            f"{BASE_URL}/api/xray",
            json={"id": xray_case["id"], "scale": 1.5},
            timeout=30,
        ),
        expected_status=404,
    )

    windowing = expect_json(
        owner.post(
            f"{BASE_URL}/api/windowing",
            json={"id": xray_case["id"], "wl": 40, "ww": 400},
            timeout=60,
        )
    )
    assert windowing["code"] == 200 and windowing["main_img_b64"].startswith("data:image/jpeg;base64,")

    xray = expect_json(
        owner.post(
            f"{BASE_URL}/api/xray",
            json={"id": xray_case["id"], "scale": 1.5},
            timeout=180,
        )
    )
    assert xray["code"] == 200 and "angles" in xray and "slips" in xray

    for audience in ("doctor", "personal"):
        report = owner.get(
            f"{BASE_URL}/api/export_pdf",
            params={"id": xray_case["id"], "audience": audience, "use_ai": 0},
            timeout=120,
        )
        assert report.status_code == 200 and report.headers["content-type"].startswith("application/pdf")
        assert report.content.startswith(b"%PDF")

    with dicom.open("rb") as handle:
        started = expect_json(
            owner.post(
                f"{BASE_URL}/api/xray/batch/start",
                files=[("files", (dicom.name, handle, "application/dicom"))],
                data={"scale": "1.5"},
                timeout=120,
            )
        )
    xray_job = poll_job(owner, started["job"]["job_id"])
    assert xray_job["status"] == "completed" and xray_job["items"]
    xray_output = xray_job["items"][0]["left_url"]
    assert owner.get(f"{BASE_URL}{xray_output}", timeout=30).status_code == 200
    assert outsider.get(f"{BASE_URL}{xray_output}", timeout=30).status_code == 403

    rgb_case = upload(owner, rgb, "rgb")
    rgb_result = expect_json(
        owner.post(f"{BASE_URL}/api/rgb", json={"id": rgb_case["id"]}, timeout=120)
    )
    assert rgb_result["code"] == 200

    with rgb.open("rb") as handle:
        started = expect_json(
            owner.post(
                f"{BASE_URL}/api/rgb/batch/start",
                files=[("files", (rgb.name, handle, "image/jpeg"))],
                timeout=120,
            )
        )
    rgb_job = poll_job(owner, started["job"]["job_id"])
    assert rgb_job["status"] == "completed" and rgb_job["items"]

    with rgb.open("rb") as handle:
        transfer = expect_json(
            owner.post(
                f"{BASE_URL}/api/pseudoct/single",
                files={"file": (rgb.name, handle, "image/jpeg")},
                data={"mode": "transfer"},
                timeout=300,
            )
        )
    transfer_url = transfer["item"]["generated_url"]
    assert owner.get(f"{BASE_URL}{transfer_url}", timeout=30).status_code == 200

    with source.open("rb") as handle:
        direct = expect_json(
            owner.post(
                f"{BASE_URL}/api/pseudoct/single",
                files={"file": (source.name, handle, "image/png")},
                data={"mode": "source_direct"},
                timeout=300,
            )
        )
    direct_url = direct["item"]["generated_url"]
    assert outsider.get(f"{BASE_URL}{direct_url}", timeout=30).status_code == 403

    loopback = expect_json(
        owner.post(
            f"{BASE_URL}/api/pseudoct/loop_to_xray",
            json={"generated_url": direct_url, "scale": 1.5},
            timeout=300,
        )
    )
    assert loopback["code"] == 200 and loopback["image_id"]

    print("SpineInsight full HTTP regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

