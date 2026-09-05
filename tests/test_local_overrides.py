from scripts.apply_local_overrides import _set_send_schedule


def test_set_send_schedule_preserves_active_block() -> None:
    workflow = (
        "on:\n"
        "  schedule:\n"
        "    - cron: \"0 0 * * *\"\n"
        "      timezone: \"Asia/Shanghai\"\n"
    )

    result = _set_send_schedule(workflow)

    assert 'cron: "43 0 * * *"' in result
    assert 'timezone: "Asia/Shanghai"' in result


def test_set_send_schedule_reenables_commented_upstream_block() -> None:
    workflow = (
        "on:\n"
        "  workflow_dispatch:\n"
        "  # schedule:\n"
        "  #   - cron: \"0 0 * * *\"\n"
        "  #     timezone: \"Asia/Shanghai\"\n"
        "\njobs:\n"
    )

    result = _set_send_schedule(workflow)

    assert "  schedule:\n" in result
    assert '    - cron: "43 0 * * *"\n' in result
    assert '      timezone: "Asia/Shanghai"\n' in result
    assert "# schedule:" not in result
