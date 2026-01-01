from apps.core import notifications
from apps.screens.startup_notifications import (
    LCD_EVENT_LOCK_FILE,
    DEFAULT_EVENT_DURATION_SECONDS,
    read_lcd_event_lock_file,
)


def test_notification_manager_writes_event_lock(tmp_path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    manager = notifications.NotificationManager(
        lock_file=lock_dir / "lcd-low",
        sticky_lock_file=lock_dir / "lcd-high",
        event_lock_file=lock_dir / LCD_EVENT_LOCK_FILE,
    )

    manager.send(
        "Subject",
        "Body",
        channel="event",
        duration_seconds=45,
    )

    payload = read_lcd_event_lock_file(lock_dir / LCD_EVENT_LOCK_FILE)

    assert payload is not None
    assert payload.subject == "Subject"
    assert payload.body == "Body"
    assert payload.duration_seconds == 45


def test_event_lock_duration_defaults_when_invalid(tmp_path):
    lock_file = tmp_path / LCD_EVENT_LOCK_FILE
    lock_file.write_text("Sub\nBody\nnot-a-number\n", encoding="utf-8")

    payload = read_lcd_event_lock_file(lock_file)

    assert payload is not None
    assert payload.duration_seconds == DEFAULT_EVENT_DURATION_SECONDS
