"""Keep the small local customizations needed by this fork after an upstream sync."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEND_WORKFLOW = ROOT / ".github" / "workflows" / "send.yml"
SENDER = ROOT / "app" / "sender.py"
LOCAL_CRON = "43 0 * * *"


def _set_send_schedule(text: str) -> str:
    lines = text.splitlines(keepends=True)
    in_schedule = False
    schedule_indent = -1

    for index, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if not in_schedule:
            if stripped == "schedule:":
                in_schedule = True
                schedule_indent = indent
            continue

        if stripped and not stripped.startswith("#") and indent <= schedule_indent:
            in_schedule = False
            continue

        line_body = line.rstrip("\r\n")
        line_ending = line[len(line_body) :]
        match = re.match(r'^(\s*-\s*cron:\s*")([^"]*)(".*)$', line_body)
        if match:
            lines[index] = f'{match.group(1)}{LOCAL_CRON}{match.group(3)}{line_ending}'
            return "".join(lines)

    # The upstream template may keep the GitHub Actions schedule commented out
    # when it expects an external cron service. Re-enable that block for this
    # fork and keep the local time instead of failing the whole sync.
    for index, line in enumerate(lines):
        line_body = line.rstrip("\r\n")
        line_ending = line[len(line_body) :]
        schedule_match = re.match(r'^(\s*)#\s*schedule:\s*$', line_body)
        if not schedule_match:
            continue

        base_indent = schedule_match.group(1)
        for cron_index in range(index + 1, min(len(lines), index + 8)):
            cron_body = lines[cron_index].rstrip("\r\n")
            cron_ending = lines[cron_index][len(cron_body) :]
            cron_match = re.match(r'^\s*#\s*-\s*cron:\s*"[^"]*"(.*)$', cron_body)
            if not cron_match:
                continue

            lines[index] = f"{base_indent}schedule:{line_ending}"
            lines[cron_index] = (
                f'{base_indent}  - cron: "{LOCAL_CRON}"{cron_match.group(1)}{cron_ending}'
            )

            for timezone_index in range(cron_index + 1, min(len(lines), cron_index + 4)):
                timezone_body = lines[timezone_index].rstrip("\r\n")
                timezone_ending = lines[timezone_index][len(timezone_body) :]
                timezone_match = re.match(r'^\s*#\s*(timezone:.*)$', timezone_body)
                if timezone_match:
                    lines[timezone_index] = (
                        f"{base_indent}    {timezone_match.group(1)}{timezone_ending}"
                    )
                    break
            return "".join(lines)

        # If the upstream keeps only a commented schedule marker, create a
        # complete active block so future upstream changes remain schedulable.
        lines[index : index + 1] = [
            f"{base_indent}schedule:{line_ending}",
            f'{base_indent}  - cron: "{LOCAL_CRON}"{line_ending}',
            f'{base_indent}    timezone: "Asia/Shanghai"{line_ending}',
        ]
        return "".join(lines)

    # Last-resort compatibility for an upstream template that removes the
    # schedule block entirely: add it below a mapping-style `on:` declaration.
    for index, line in enumerate(lines):
        if line.strip() != "on:":
            continue
        indent = line[: len(line) - len(line.lstrip())]
        ending = "\n" if line.endswith("\n") else ""
        lines[index + 1 : index + 1] = [
            f"{indent}  schedule:{ending}",
            f'{indent}    - cron: "{LOCAL_CRON}"{ending}',
            f'{indent}      timezone: "Asia/Shanghai"{ending}',
        ]
        return "".join(lines)

    raise RuntimeError("无法在 .github/workflows/send.yml 中定位 schedule/cron")


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


def main() -> None:
    send_text = SEND_WORKFLOW.read_text(encoding="utf-8")
    sender_text = SENDER.read_text(encoding="utf-8")

    updated_send = _set_send_schedule(send_text)
    updated_sender = _set_native_emoji_confirmation(sender_text)

    SEND_WORKFLOW.write_text(updated_send, encoding="utf-8")
    SENDER.write_text(updated_sender, encoding="utf-8")
    print(f"已保留续火花定时: {LOCAL_CRON} Asia/Shanghai")
    print("已保留原生表情文本发送确认兼容")


if __name__ == "__main__":
    main()
