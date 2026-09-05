from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_PATHS = (
    "scripts/apply_local_overrides.py",
    "scripts/keepalive.py",
    "scripts/resolve_sync_conflicts.py",
    "tests/test_local_overrides.py",
    "tests/test_sync_integration.py",
)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise AssertionError(f"git {' '.join(args)} 失败: {detail}")
    return result


def _git_output(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _commit(repo: Path, message: str, *, date: str | None = None) -> str:
    _git(repo, "add", "-A")
    env = None
    if date:
        env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        env=None if env is None else {**os.environ, **env},
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise AssertionError(f"创建测试提交失败: {detail}")
    return _git_output(repo, "rev-parse", "HEAD")


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "sync integration test")
    _git(repo, "config", "user.email", "sync-integration@example.invalid")


def _copy_candidate_files(repo: Path) -> None:
    for relative in (".github/workflows", "scripts", "tests"):
        (repo / relative).mkdir(parents=True, exist_ok=True)
    (repo / "app").mkdir(parents=True, exist_ok=True)

    for workflow in (PROJECT_ROOT / ".github/workflows").glob("*.yml"):
        shutil.copy2(workflow, repo / ".github/workflows" / workflow.name)
    for relative in PROTECTED_PATHS:
        shutil.copy2(PROJECT_ROOT / relative, repo / relative)

    (repo / "app/sender.py").write_text(
        """async def confirm(content, expectedText):
    return normalize(content.innerText).includes(normalize(expectedText));
""",
        encoding="utf-8",
    )


def _workflow_tree(repo: Path, ref: str) -> str:
    return _git_output(repo, "rev-parse", f"{ref}:.github/workflows")


def _show(repo: Path, ref: str, path: str) -> str:
    return _git_output(repo, "show", f"{ref}:{path}")


def _run_overrides(repo: Path, base_sha: str, *, verify_only: bool = False) -> None:
    args = [
        sys.executable,
        str(repo / "scripts/apply_local_overrides.py"),
        "--base-sha",
        base_sha,
    ]
    if verify_only:
        args.append("--verify-only")
    result = subprocess.run(args, cwd=repo, check=False, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise AssertionError(f"本地同步脚本失败: {detail}")


def _restore_protected_paths(repo: Path, base_sha: str) -> None:
    _git(repo, "checkout", base_sha, "--", *PROTECTED_PATHS)


def _finish_merge(repo: Path, base_sha: str) -> str:
    _restore_protected_paths(repo, base_sha)
    _run_overrides(repo, base_sha)
    _git(repo, "add", "--update", "--", "app/sender.py")
    candidate_tree = _git_output(repo, "write-tree")
    _run_overrides(repo, base_sha, verify_only=True)
    assert not _git(repo, "diff", "--name-only").stdout.strip()
    assert not _git(repo, "ls-files", "--others", "--exclude-standard").stdout.strip()
    assert _git_output(repo, "write-tree") == candidate_tree
    _git(repo, "commit", "--no-edit")
    final_sha = _git_output(repo, "rev-parse", "HEAD")
    assert _git_output(repo, "rev-parse", "HEAD^{tree}") == candidate_tree
    assert len(_git_output(repo, "rev-list", "--parents", "-1", final_sha).split()) == 3
    return final_sha


def test_two_consecutive_syncs_preserve_workflows_and_resolve_new_workflow_conflict(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_candidate_files(repo)
    base_sha = _commit(repo, "local fork base")

    (repo / "fork-only.txt").write_text("local fork change\n", encoding="utf-8")
    local_base_sha = _commit(repo, "local fork divergence")
    _git(repo, "branch", "upstream-main", base_sha)

    _git(repo, "switch", "upstream-main")
    send_workflow = (repo / ".github/workflows/send.yml").read_text(encoding="utf-8")
    send_workflow = send_workflow.replace(
        '  schedule:\n    # 北京时间每天 00:43，避开整点调度高峰\n'
        '    - cron: "43 0 * * *"\n      timezone: "Asia/Shanghai"\n',
        '  # schedule:\n  #   - cron: "0 0 * * *"\n'
        '  #     timezone: "Asia/Shanghai"\n',
    )
    (repo / ".github/workflows/send.yml").write_text(send_workflow, encoding="utf-8")
    (repo / ".github/workflows/docker.yml").write_text(
        "name: upstream docker\n\non: workflow_dispatch\n", encoding="utf-8"
    )
    (repo / "app/upstream_feature.py").write_text("UPSTREAM = True\n", encoding="utf-8")
    _git(repo, "rm", *PROTECTED_PATHS)
    upstream_one_sha = _commit(repo, "upstream adds docker workflow")

    _git(repo, "switch", "main")
    _git(repo, "merge", "--no-commit", "--no-ff", "-X", "theirs", upstream_one_sha)
    first_sha = _finish_merge(repo, local_base_sha)

    base_workflow_tree = _workflow_tree(repo, local_base_sha)
    assert _workflow_tree(repo, first_sha) == base_workflow_tree
    assert _show(repo, first_sha, ".github/workflows/send.yml") == _show(
        repo, local_base_sha, ".github/workflows/send.yml"
    )
    assert _git(
        repo, "cat-file", "-e", f"{first_sha}:.github/workflows/docker.yml", check=False
    ).returncode
    assert _show(repo, first_sha, "app/upstream_feature.py") == "UPSTREAM = True"
    first_sender = _show(repo, first_sha, "app/sender.py")
    assert "querySelectorAll('[title], [aria-label], [alt]')" in first_sender

    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", "main")

    _git(repo, "switch", "upstream-main")
    (repo / ".github/workflows/docker.yml").write_text(
        "name: upstream docker v2\n\non: workflow_dispatch\n", encoding="utf-8"
    )
    upstream_two_sha = _commit(repo, "upstream modifies excluded docker workflow")
    _git(repo, "switch", "main")
    merge = _git(
        repo,
        "merge",
        "--no-commit",
        "--no-ff",
        "-X",
        "theirs",
        upstream_two_sha,
        check=False,
    )
    assert merge.returncode != 0
    assert _git(repo, "diff", "--name-only", "--diff-filter=U").stdout.strip() == ".github/workflows/docker.yml"

    resolver = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/resolve_sync_conflicts.py"),
            "--repo-root",
            str(repo),
            "--base-sha",
            first_sha,
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert resolver.returncode == 0, resolver.stderr or resolver.stdout
    second_sha = _finish_merge(repo, first_sha)
    assert _workflow_tree(repo, second_sha) == base_workflow_tree
    assert _show(repo, second_sha, ".github/workflows/send.yml") == _show(
        repo, first_sha, ".github/workflows/send.yml"
    )
    assert _git(
        repo, "cat-file", "-e", f"{second_sha}:.github/workflows/docker.yml", check=False
    ).returncode
    assert "querySelectorAll('[title], [aria-label], [alt]')" in _show(
        repo, second_sha, "app/sender.py"
    )
    assert len(_git_output(repo, "rev-list", "--parents", "-1", second_sha).split()) == 3

    _git(repo, "push", "origin", "main")
    remote_sha = _git_output(repo, "ls-remote", "origin", "refs/heads/main").split()[0]
    assert remote_sha == second_sha
    assert _workflow_tree(repo, remote_sha) == base_workflow_tree

    # A normal application modify/delete conflict must remain a real failure.
    # Only the excluded workflow/local-sync paths are eligible for BASE resolution.
    _git(repo, "switch", "main")
    _git(repo, "rm", "--", "app/upstream_feature.py")
    conflict_base_sha = _commit(repo, "local application divergence")
    _git(repo, "switch", "upstream-main")
    (repo / "app/upstream_feature.py").write_text("UPSTREAM = False\n", encoding="utf-8")
    upstream_conflict_sha = _commit(repo, "upstream application conflict")
    _git(repo, "switch", "main")
    conflict_merge = _git(
        repo,
        "merge",
        "--no-commit",
        "--no-ff",
        "-X",
        "theirs",
        upstream_conflict_sha,
        check=False,
    )
    assert conflict_merge.returncode != 0
    conflict_resolver = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/resolve_sync_conflicts.py"),
            "--repo-root",
            str(repo),
            "--base-sha",
            conflict_base_sha,
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert conflict_resolver.returncode != 0
    _git(repo, "merge", "--abort")
    assert not _git(repo, "status", "--porcelain").stdout.strip()


def test_keepalive_skips_recent_main_and_runs_once_after_thirty_days(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(
        PROJECT_ROOT / ".github/workflows/send.yml", repo / ".github/workflows/send.yml"
    )
    shutil.copy2(PROJECT_ROOT / "scripts/keepalive.py", repo / "scripts/keepalive.py")
    base_sha = _commit(repo, "recent main", date="2026-01-01T00:00:00Z")

    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", "main")
    recent_tree = _workflow_tree(repo, base_sha)

    recent = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/keepalive.py"),
            "--base-sha",
            base_sha,
            "--now-epoch",
            str(1767225600 + 29 * 24 * 60 * 60),
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert recent.returncode == 0, recent.stderr or recent.stdout
    assert _git_output(repo, "rev-parse", "HEAD") == base_sha

    residual = repo / "residual.txt"
    residual.write_text("must not be committed\n", encoding="utf-8")
    _git(repo, "add", "--", "residual.txt")
    dirty = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/keepalive.py"),
            "--base-sha",
            base_sha,
            "--now-epoch",
            str(1767225600 + 31 * 24 * 60 * 60),
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert dirty.returncode != 0
    assert _git_output(repo, "rev-parse", "HEAD") == base_sha
    _git(repo, "restore", "--staged", "--", "residual.txt")
    residual.unlink()

    due = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/keepalive.py"),
            "--base-sha",
            base_sha,
            "--now-epoch",
            str(1767225600 + 31 * 24 * 60 * 60),
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert due.returncode == 0, due.stderr or due.stdout
    keepalive_sha = _git_output(repo, "rev-parse", "HEAD")
    assert keepalive_sha != base_sha
    assert _workflow_tree(repo, keepalive_sha) == recent_tree
    assert _git_output(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == keepalive_sha

    second = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/keepalive.py"),
            "--base-sha",
            keepalive_sha,
            "--now-epoch",
            str(1767225600 + 32 * 24 * 60 * 60),
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, second.stderr or second.stdout
    assert _git_output(repo, "rev-parse", "HEAD") == keepalive_sha
