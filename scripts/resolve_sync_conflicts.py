"""Resolve only the local paths that are intentionally excluded from upstream."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


WORKFLOW_PREFIX = ".github/workflows/"
PROTECTED_PATHS = {
    "scripts/apply_local_overrides.py",
    "scripts/keepalive.py",
    "scripts/resolve_sync_conflicts.py",
    "tests/test_local_overrides.py",
    "tests/test_sync_integration.py",
}


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} 失败: {detail}")
    return result


def _unmerged_paths(repo_root: Path) -> list[str]:
    output = _git(
        repo_root,
        "diff",
        "--name-only",
        "--diff-filter=U",
        "-z",
    ).stdout
    return [path for path in output.split("\0") if path]


def _is_protected(path: str) -> bool:
    return path.startswith(WORKFLOW_PREFIX) or path in PROTECTED_PATHS


def _exists_at(repo_root: Path, base_sha: str, path: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{base_sha}:{path}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    ).returncode == 0


def resolve(repo_root: Path, base_sha: str) -> None:
    conflicts = _unmerged_paths(repo_root)
    if not conflicts:
        raise RuntimeError("merge 失败但 index 没有未解决冲突，拒绝猜测并继续")

    other_conflicts = [path for path in conflicts if not _is_protected(path)]
    if other_conflicts:
        raise RuntimeError(
            "存在未授权的上游冲突，必须真实失败: " + ", ".join(other_conflicts)
        )

    for path in conflicts:
        if _exists_at(repo_root, base_sha, path):
            _git(repo_root, "checkout", base_sha, "--", path)
            _git(repo_root, "add", "--", path)
        else:
            _git(repo_root, "rm", "--", path)

    remaining = _unmerged_paths(repo_root)
    if remaining:
        raise RuntimeError("保护路径冲突未能全部按 BASE 解决: " + ", ".join(remaining))

    print("已按同步前 BASE 解决受保护路径冲突: " + ", ".join(conflicts))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--base-sha", required=True)
    args = parser.parse_args(argv)

    try:
        resolve(args.repo_root.resolve(), args.base_sha)
    except RuntimeError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
