from __future__ import annotations

import os
import sys
from pathlib import Path

import urllib3
from dulwich import porcelain
from dulwich.repo import Repo


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    remote_url = os.environ["SITES_REMOTE_URL"]
    branch = os.environ.get("SITES_BRANCH", "main")
    token = os.environ["SITES_TOKEN"]

    if not (root / ".git").exists():
        Repo.init(root, default_branch=branch.encode("utf-8"))

    porcelain.add(root)
    commit_sha = porcelain.commit(
        root,
        message=b"Publish SpineInsight production frontend",
        author=b"SpineInsight Release Bot <release@spineinsight.local>",
        committer=b"SpineInsight Release Bot <release@spineinsight.local>",
    )

    pool = urllib3.PoolManager(
        headers={"Authorization": f"Bearer {token}"},
        timeout=urllib3.Timeout(connect=20, read=120),
    )
    porcelain.push(
        root,
        remote_url,
        refspecs=[f"HEAD:refs/heads/{branch}"],
        pool_manager=pool,
    )
    print(commit_sha.decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
