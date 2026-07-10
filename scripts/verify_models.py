from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = ROOT / "weights"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    manifest = json.loads((WEIGHTS / "manifest.json").read_text(encoding="utf-8"))
    failures = []
    for item in manifest["models"]:
        path = WEIGHTS / item["path"]
        if not path.is_file():
            failures.append(f"missing: {item['path']}")
            continue
        actual = sha256(path)
        if actual.lower() != item["sha256"].lower():
            failures.append(f"checksum mismatch: {item['path']}")
        else:
            print(f"ok {item['task']}: {item['path']}")
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

