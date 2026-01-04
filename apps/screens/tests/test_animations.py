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


def test_low_channel_respects_explicit_animation(tmp_path):
    animation_file = tmp_path / "custom.txt"
    animation_file.write_text("A" * animations.ANIMATION_FRAME_CHARS + "\n", encoding="utf-8")

    payload = lcd_screen.LockPayload(
        "subject",
        "body",
        123,
        animation_name=str(animation_file),
    )

    animation_payload = lcd_screen._select_low_payload(payload)

    assert animation_payload.line1 == "A" * lcd_screen.LCD_COLUMNS
    assert animation_payload.line2 == "A" * lcd_screen.LCD_COLUMNS
    assert animation_payload.scroll_ms == 123


def test_low_channel_without_text_or_animation_is_blank():
    payload = lcd_screen.LockPayload("", "", lcd_screen.DEFAULT_SCROLL_MS)

    assert lcd_screen._select_low_payload(payload) == payload
