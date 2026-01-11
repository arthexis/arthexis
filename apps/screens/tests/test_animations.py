import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from apps.screens import animations
from apps.screens import lcd_screen


@pytest.fixture
def uptime_mocks(monkeypatch, tmp_path):
    base_dir = tmp_path
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()

    now = datetime(2024, 2, 1, 12, 0, tzinfo=timezone.utc)
    started_at = now - timedelta(hours=1)
    lock_path = lock_dir / lcd_screen.SUITE_UPTIME_LOCK_NAME
    lock_path.write_text(json.dumps({"started_at": started_at.isoformat()}), encoding="utf-8")
    lock_path.touch()
    lock_path_timestamp = now.timestamp()
    os.utime(lock_path, (lock_path_timestamp, lock_path_timestamp))

    install_date = now - timedelta(hours=2)
    install_lock = lock_dir / lcd_screen.INSTALL_DATE_LOCK_NAME
    install_lock.write_text(install_date.isoformat(), encoding="utf-8")

    monkeypatch.setattr(lcd_screen.psutil, "boot_time", lambda: (now - timedelta(hours=2)).timestamp())
    monkeypatch.setattr(lcd_screen, "_ap_mode_enabled", lambda: False)
    monkeypatch.setattr(lcd_screen, "_internet_interface_label", lambda: "eth0")

    return base_dir, now


def test_default_tree_frames_are_complete():
    frames = animations.default_tree_frames()
    assert frames, "Expected bundled animation frames"
    assert all(len(frame) == animations.ANIMATION_FRAME_CHARS for frame in frames)


def test_loading_animation_enforces_width(tmp_path):
    bad_file = tmp_path / "bad.txt"
    bad_file.write_text("too short\n", encoding="utf-8")

    with pytest.raises(animations.AnimationLoadError):
        animations.load_frames_from_file(bad_file)


def test_low_channel_gaps_use_uptime_payload(uptime_mocks):
    base_dir, now = uptime_mocks
    payload = lcd_screen.LockPayload("", "", lcd_screen.DEFAULT_SCROLL_MS)

    uptime_payload = lcd_screen._select_low_payload(
        payload,
        base_dir=base_dir,
        now=now,
    )

    assert uptime_payload.line1 == "UP 0d1h0m"
    assert uptime_payload.line2 == "ON 60m0s eth0"
    assert uptime_payload.scroll_ms == lcd_screen.DEFAULT_SCROLL_MS


def test_low_channel_whitespace_payload_uses_uptime_payload(uptime_mocks):
    base_dir, now = uptime_mocks
    payload = lcd_screen.LockPayload("   ", "\t", lcd_screen.DEFAULT_SCROLL_MS)

    uptime_payload = lcd_screen._select_low_payload(
        payload,
        base_dir=base_dir,
        now=now,
    )

    assert uptime_payload.line1 == "UP 0d1h0m"
    assert uptime_payload.line2 == "ON 60m0s eth0"
    assert uptime_payload.scroll_ms == lcd_screen.DEFAULT_SCROLL_MS
