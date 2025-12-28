from itertools import cycle

import pytest

from apps.screens import animations
from apps.screens import lcd_screen


def test_default_tree_frames_are_complete():
    frames = animations.default_tree_frames()
    assert frames, "Expected bundled animation frames"
    assert all(len(frame) == animations.ANIMATION_FRAME_CHARS for frame in frames)


def test_loading_animation_enforces_width(tmp_path):
    bad_file = tmp_path / "bad.txt"
    bad_file.write_text("too short\n", encoding="utf-8")

    with pytest.raises(animations.AnimationLoadError):
        animations.load_frames_from_file(bad_file)


def test_low_channel_gaps_use_animation():
    frame_cycle = cycle(["A" * animations.ANIMATION_FRAME_CHARS])
    payload = lcd_screen.LockPayload("", "", lcd_screen.DEFAULT_SCROLL_MS)

    animation_payload = lcd_screen._select_low_payload(
        payload,
        frame_cycle=frame_cycle,
        scroll_ms=123,
        frames_per_payload=1,
    )

    assert animation_payload.line1 == "A" * lcd_screen.LCD_COLUMNS
    assert animation_payload.line2 == "A" * lcd_screen.LCD_COLUMNS
    assert animation_payload.scroll_ms == 123
