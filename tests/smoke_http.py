from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw


BASE_URL = os.getenv("MINDSPINE_TEST_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def synthetic_image() -> bytes:
    image = Image.new("RGB", (512, 768), (12, 14, 20))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((190, 40, 322, 730), radius=56, fill=(195, 198, 205))
    for y in range(90, 690, 70):
        draw.ellipse((220, y, 292, y + 45), fill=(245, 245, 245))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def upload(session: requests.Session, payload: bytes, mode: str) -> dict:
    response = session.post(
        f"{BASE_URL}/api/upload",
        files={"file": ("synthetic.png", payload, "image/png")},
        data={"mode": mode},
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()
    assert result["code"] == 200
    return result


def main() -> int:
    payload = synthetic_image()
    session_a = requests.Session()
    session_b = requests.Session()

    live = session_a.get(f"{BASE_URL}/health/live", timeout=10)
    live.raise_for_status()

    case_a = upload(session_a, payload, "rgb")
    case_b = upload(session_b, payload, "rgb")

    cross = session_a.post(
        f"{BASE_URL}/api/rgb",
        json={"id": case_b["id"]},
        timeout=60,
    )
    assert cross.status_code == 404, cross.text

    own = session_a.post(
        f"{BASE_URL}/api/rgb",
        json={"id": case_a["id"]},
        timeout=120,
    )
    own.raise_for_status()
    assert own.json()["code"] == 200

    generated = session_a.post(
        f"{BASE_URL}/api/pseudoct/single",
        files={"file": ("synthetic.png", payload, "image/png")},
        data={"mode": "source_direct"},
        timeout=300,
    )
    generated.raise_for_status()
    generated_url = generated.json()["item"]["generated_url"]
    assert session_a.get(f"{BASE_URL}{generated_url}", timeout=30).status_code == 200
    assert session_b.get(f"{BASE_URL}{generated_url}", timeout=30).status_code == 403

    print("SpineInsight HTTP smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

