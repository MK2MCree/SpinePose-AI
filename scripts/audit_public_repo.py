from __future__ import annotations

import re
from pathlib import Path

from dulwich.repo import Repo


PATTERNS = {
    "provider_key": re.compile(
        r"(?i)(?:sk-|api[_-]?key\s*[:=]\s*[\"'])[A-Za-z0-9_-]{20,}"
    ),
    "private_key": re.compile(r"BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY"),
    "bearer_token": re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{30,}"),
}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    repo = Repo(root)
    hits: list[tuple[str, str]] = []

    for raw_path in repo.open_index():
        relative = raw_path.decode("utf-8")
        path = root / relative
        try:
            text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name, pattern in PATTERNS.items():
            if pattern.search(text):
                hits.append((relative, name))

    print(f"sensitive-pattern-hits: {len(hits)}")
    for relative, name in hits:
        print(f"{name}: {relative}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
