from pathlib import Path

from apps.core.notifications import NotificationManager
from apps.screens.startup_notifications import (
    LCD_LOW_LOCK_FILE,
    LCD_RUNTIME_LOCK_FILE,
    render_lcd_lock_file,
)


def test_nonzero_channel_uses_numbered_lock_file(tmp_path: Path) -> None:
    (tmp_path / LCD_RUNTIME_LOCK_FILE).write_text("", encoding="utf-8")
    manager = NotificationManager(lock_dir=tmp_path)

    manager.send("Subject", "Body", channel_type="low", channel_num=3)

    expected_lock = tmp_path / f"{LCD_LOW_LOCK_FILE}-3"
    unexpected_lock = tmp_path / LCD_LOW_LOCK_FILE

    assert expected_lock.exists()
    assert not unexpected_lock.exists()
    assert expected_lock.read_text(encoding="utf-8") == render_lcd_lock_file(
        subject="Subject", body="Body"
    )


def test_send_skips_lock_file_when_lcd_disabled(tmp_path: Path) -> None:
    manager = NotificationManager(lock_dir=tmp_path)

    manager.send("Subject", "Body")

    expected_lock = tmp_path / LCD_LOW_LOCK_FILE
    assert not expected_lock.exists()
