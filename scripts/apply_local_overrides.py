"""Keep and verify the small local customizations needed by this fork."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = Path(".github/workflows")
SEND_WORKFLOW = ROOT / ".github" / "workflows" / "send.yml"
SENDER = ROOT / "app" / "sender.py"
LOCAL_CRON = "43 0 * * *"
LOCAL_TIMEZONE = "Asia/Shanghai"


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} 失败: {detail}")
    return result


def _workflow_manifest(ref: str) -> list[str]:
    return _git(
        "ls-tree",
        "-r",
        "--full-tree",
        ref,
        "--",
        str(WORKFLOW_ROOT),
    ).stdout.splitlines()


def _unmerged_paths() -> list[str]:
    return _git("diff", "--name-only", "--diff-filter=U").stdout.splitlines()


def _assert_workflow_tree(base_sha: str) -> None:
    unmerged = _unmerged_paths()
    if unmerged:
        raise RuntimeError(f"index 仍有未解决冲突: {', '.join(unmerged)}")

    base_manifest = _workflow_manifest(base_sha)
    index_tree = _git("write-tree").stdout.strip()
    index_manifest = _workflow_manifest(index_tree)
    if index_manifest != base_manifest:
        raise RuntimeError("最终 index 的 .github/workflows 与同步前不一致")

    for args in (
        ("diff", "--quiet", base_sha, "--", str(WORKFLOW_ROOT)),
        ("diff", "--cached", "--quiet", base_sha, "--", str(WORKFLOW_ROOT)),
    ):
        if _git(*args, check=False).returncode:
            raise RuntimeError("工作区或 index 中仍有 workflow 改动")

    untracked = _git(
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        str(WORKFLOW_ROOT),
    ).stdout.strip()
    if untracked:
        raise RuntimeError(f"存在未跟踪 workflow 文件: {untracked}")


def _restore_local_workflow_tree(base_sha: str) -> None:
    # --staged --worktree is important here: an upstream-only workflow can
    # already be staged by the stopped merge, so a later git rm is fragile.
    _git(
        "restore",
        f"--source={base_sha}",
        "--staged",
        "--worktree",
        "--",
        str(WORKFLOW_ROOT),
    )
    _assert_workflow_tree(base_sha)


def _validate_send_schedule(text: str) -> None:
    """Validate the local schedule without rewriting the send workflow."""

    on_indent: int | None = None
    schedule_indent: int | None = None
    found_cron = False
    found_timezone = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if on_indent is None:
            if stripped == "on:":
                on_indent = indent
            continue

        if schedule_indent is None:
            if indent <= on_indent:
                break
            if stripped == "schedule:":
                schedule_indent = indent
            continue

        if indent <= schedule_indent:
            break

        cron_match = re.match(r"^-\s*cron:\s*[\"']([^\"']+)[\"']$", stripped)
        if cron_match and cron_match.group(1) == LOCAL_CRON:
            found_cron = True

        timezone_match = re.match(r"^timezone:\s*[\"']([^\"']+)[\"']$", stripped)
        if timezone_match and timezone_match.group(1) == LOCAL_TIMEZONE:
            found_timezone = True

    if not found_cron or not found_timezone:
        raise RuntimeError(
            "本地 send.yml 必须保留 schedule: 43 0 * * * / Asia/Shanghai"
        )


def _set_native_emoji_confirmation(text: str) -> str:
    # Upstream may eventually include this behavior itself. Detect the
    # attribute-based confirmation before applying the local compatibility code.
    if (
        "content.getAttribute('title')" in text
        and "querySelectorAll('[title], [aria-label], [alt]')" in text
    ):
        return text

    old = "return normalize(content.innerText).includes(normalize(expectedText));"
    new = """const renderedText = [
                        content.innerText,
                        content.getAttribute('title'),
                        content.getAttribute('aria-label'),
                        ...[...content.querySelectorAll('[title], [aria-label], [alt]')].flatMap(element => [
                            element.getAttribute('title'),
                            element.getAttribute('aria-label'),
                            element.getAttribute('alt'),
                        ]),
                    ].filter(Boolean).map(normalize);
                    return renderedText.some(value => value.includes(normalize(expectedText)));"""
    if old not in text:
        raise RuntimeError(
            "上游 sender.py 的文字确认逻辑已变化，无法自动恢复原生表情文本兼容"
        )
    return text.replace(old, new, 1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-sha",
        help="同步前 main 的提交，用于恢复并校验本地 workflow 目录",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="只校验，不恢复 workflow 或修改 sender.py",
    )
    args = parser.parse_args(argv)

    if args.verify_only:
        if not args.base_sha:
            parser.error("--verify-only 必须同时提供 --base-sha")
        _assert_workflow_tree(args.base_sha)
    elif args.base_sha:
        _restore_local_workflow_tree(args.base_sha)

    send_text = SEND_WORKFLOW.read_text(encoding="utf-8")
    _validate_send_schedule(send_text)
    if args.verify_only:
        print(f"已验证续火花定时: {LOCAL_CRON} {LOCAL_TIMEZONE}")
        return

    sender_text = SENDER.read_text(encoding="utf-8")
    updated_sender = _set_native_emoji_confirmation(sender_text)
    if updated_sender != sender_text:
        SENDER.write_text(updated_sender, encoding="utf-8")
    print(f"已验证续火花定时: {LOCAL_CRON} {LOCAL_TIMEZONE}")
    print("已保留原生表情文本发送确认兼容")


if __name__ == "__main__":
    main()
