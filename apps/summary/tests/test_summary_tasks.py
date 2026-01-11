from pathlib import Path

import pytest

from apps.summary.tasks import _write_lcd_frames
from apps.summary.services import render_lcd_payload

pytestmark = [
    pytest.mark.role("Terminal"),
    pytest.mark.role("Control"),
]


def test_write_lcd_frames_updates_lock_file(tmp_path):
    lock_file = tmp_path / "lcd-low"
    frames = [("HELLO", "WORLD"), ("LAST", "FRAME")]

    _write_lcd_frames(
        frames,
        lock_file=lock_file,
        sleep_seconds=0,
        sleep_fn=lambda *_args, **_kwargs: None,
    )

    assert lock_file.exists()
    expected = render_lcd_payload("LAST", "FRAME")
    assert lock_file.read_text(encoding="utf-8") == expected
