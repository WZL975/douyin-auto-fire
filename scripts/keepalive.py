"""Create an infrequent empty commit so public scheduled workflows stay active."""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path


KEEPALIVE_DAYS = 30
KEEPALIVE_MESSAGE = "chore: keep GitHub Actions schedule active"


def _git(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} 失败: {detail}")
    return result.stdout.strip()


def run(repo_root: Path, base_sha: str, now_epoch: int | None = None) -> bool:
    head_sha = _git(repo_root, "rev-parse", "HEAD")
    remote_sha = _git(repo_root, "rev-parse", "origin/main")
    if head_sha != base_sha or remote_sha != base_sha:
        raise RuntimeError("main 在保活期间发生变化，拒绝基于旧提交创建或推送空提交")

    latest_epoch = int(_git(repo_root, "show", "-s", "--format=%ct", remote_sha))
    current_epoch = int(time.time() if now_epoch is None else now_epoch)
    age = current_epoch - latest_epoch
    threshold = KEEPALIVE_DAYS * 24 * 60 * 60
    if age < threshold:
        print(f"main 最新提交距今 {max(age, 0)} 秒，未满 {KEEPALIVE_DAYS} 天，跳过保活")
        return False

    status = _git(repo_root, "status", "--porcelain")
    if status:
        raise RuntimeError("创建保活提交前工作区或 index 不干净，拒绝把残留改动带入提交")

    _git(repo_root, "commit", "--allow-empty", "-m", KEEPALIVE_MESSAGE)
    _git(repo_root, "push", "origin", "HEAD:main")
    print(f"main 已超过 {KEEPALIVE_DAYS} 天无活动，已创建并推送一次保活提交")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--now-epoch", type=int)
    args = parser.parse_args(argv)

    try:
        run(Path.cwd(), args.base_sha, args.now_epoch)
    except RuntimeError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
