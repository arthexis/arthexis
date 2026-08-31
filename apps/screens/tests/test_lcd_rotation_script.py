from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.screens.lcd_screen import locks, runner
from apps.screens.management.commands.lcd_actions.plan import Command as PlanCommand


def test_rotation_script_parser_accepts_frame_statements() -> None:
    payloads = locks.parse_rotation_script(
        """
        # Lines are comments or data-only frame statements.
        frame "FIRST" "CHECK SSH"
        frame "SECOND"
        """
    )

    assert [(payload.line1, payload.line2) for payload in payloads] == [
        ("FIRST", "CHECK SSH"),
        ("SECOND", ""),
    ]


def test_rotation_script_parser_rejects_unknown_commands() -> None:
    with pytest.raises(locks.RotationScriptError, match="unsupported command"):
        locks.parse_rotation_script('shell "rm -rf /"')


def test_rotation_script_overrides_default_rotation(monkeypatch, tmp_path) -> None:
    now_dt = datetime(2026, 6, 4, tzinfo=timezone.utc)
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / locks.ROTATION_SCRIPT_LOCK_NAME).write_text(
        'frame "SCRIPT 1" "LINE A"\nframe "SCRIPT 2" "LINE B"\n',
        encoding="utf-8",
    )
    (lock_dir / "lcd-low").write_text("Status\n0 failed units\n", encoding="utf-8")

    monkeypatch.setattr(runner.locks, "LOCK_DIR", lock_dir)
    monkeypatch.setattr(runner.locks, "_load_channel_order", lambda lock_dir: None)

    channel_info, channel_text = runner._load_channel_states({}, now_dt)
    coordinator = runner.LCDRunner()
    coordinator.configure_rotation_order(channel_info, channel_text)

    assert channel_text[locks.ROTATION_SCRIPT_CHANNEL_NAME] is True
    assert coordinator.rotation.order == (locks.ROTATION_SCRIPT_CHANNEL_NAME,)
    first = coordinator.payload_for_state(
        coordinator.rotation.order, 0, channel_info, channel_text, now_dt
    )
    second = coordinator.payload_for_state(
        coordinator.rotation.order, 0, channel_info, channel_text, now_dt
    )
    assert (first.line1, first.line2) == ("SCRIPT 1", "LINE A")
    assert (second.line1, second.line2) == ("SCRIPT 2", "LINE B")


def test_lcd_plan_previews_active_rotation_script(tmp_path) -> None:
    start_dt = datetime(2026, 6, 4, tzinfo=timezone.utc)
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / locks.ROTATION_SCRIPT_LOCK_NAME).write_text(
        'frame "SCRIPT 1" "LINE A"\nframe "SCRIPT 2" "LINE B"\n',
        encoding="utf-8",
    )
    (lock_dir / "lcd-low").write_text("Status\n0 failed units\n", encoding="utf-8")

    frames = list(
        PlanCommand()._iter_frames(
            duration=12,
            base_dir=tmp_path,
            start_dt=start_dt,
        )
    )

    rendered = [
        (frame.label, frame.line1.strip(), frame.line2.strip()) for frame in frames
    ]
    assert rendered[0] == ("script", "SCRIPT 1", "LINE A")
    assert ("script", "SCRIPT 2", "LINE B") in rendered


def test_invalid_rotation_script_falls_back_to_default_rotation(
    monkeypatch, tmp_path
) -> None:
    now_dt = datetime(2026, 6, 4, tzinfo=timezone.utc)
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / locks.ROTATION_SCRIPT_LOCK_NAME).write_text(
        'frame "too" "many" "parts"\n',
        encoding="utf-8",
    )
    (lock_dir / "lcd-low").write_text("Status\n0 failed units\n", encoding="utf-8")

    monkeypatch.setattr(runner.locks, "LOCK_DIR", lock_dir)
    monkeypatch.setattr(runner.locks, "_load_channel_order", lambda lock_dir: None)

    channel_info, channel_text = runner._load_channel_states({}, now_dt)
    coordinator = runner.LCDRunner()
    coordinator.configure_rotation_order(channel_info, channel_text)

    assert channel_text[locks.ROTATION_SCRIPT_CHANNEL_NAME] is False
    assert coordinator.rotation.order == ("low", "stats", "clock")
