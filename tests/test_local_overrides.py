import pytest

from scripts.apply_local_overrides import (
    _set_native_emoji_confirmation,
    _validate_send_schedule,
)


def test_validate_send_schedule_accepts_local_schedule() -> None:
    workflow = (
        "on:\n"
        "  workflow_dispatch:\n"
        "  schedule:\n"
        "    - cron: \"43 0 * * *\"\n"
        "      timezone: \"Asia/Shanghai\"\n"
    )

    _validate_send_schedule(workflow)


@pytest.mark.parametrize(
    "workflow",
    [
        (
            "on:\n"
            "  schedule:\n"
            "    - cron: \"0 0 * * *\"\n"
            "      timezone: \"Asia/Shanghai\"\n"
        ),
        (
            "on:\n"
            "  workflow_dispatch:\n"
            "  # schedule:\n"
            "  #   - cron: \"43 0 * * *\"\n"
            "  #     timezone: \"Asia/Shanghai\"\n"
        ),
    ],
)
def test_validate_send_schedule_rejects_nonlocal_schedule(workflow: str) -> None:
    with pytest.raises(RuntimeError, match="43 0"):
        _validate_send_schedule(workflow)


def test_native_emoji_confirmation_is_idempotent() -> None:
    sender = "return normalize(content.innerText).includes(normalize(expectedText));"

    once = _set_native_emoji_confirmation(sender)
    twice = _set_native_emoji_confirmation(once)

    assert once == twice
    assert once.count("const renderedText = [") == 1
