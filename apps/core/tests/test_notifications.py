from pathlib import Path

import pytest

from apps.core.notifications import NotificationManager
from apps.screens.startup_notifications import (
    LCD_LOW_LOCK_FILE,
    render_lcd_lock_file,
)

pytestmark = [
    pytest.mark.role("Terminal"),
    pytest.mark.role("Control"),
]


def test_nonzero_channel_uses_default_lock_file(tmp_path: Path) -> None:
    manager = NotificationManager(lock_dir=tmp_path)

    manager.send("Subject", "Body", channel_type="low", channel_num=3)

    expected_lock = tmp_path / LCD_LOW_LOCK_FILE
    unexpected_lock = tmp_path / f"{LCD_LOW_LOCK_FILE}-3"

    assert expected_lock.exists()
    assert not unexpected_lock.exists()
    assert expected_lock.read_text(encoding="utf-8") == render_lcd_lock_file(
        subject="Subject", body="Body"
    )
