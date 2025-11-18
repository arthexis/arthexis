import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.core.management import call_command  # noqa: E402
from django.core.management.base import CommandError  # noqa: E402
from django.conf import settings  # noqa: E402


pytestmark = [
    pytest.mark.role("Terminal"),
    pytest.mark.role("Control"),
    pytest.mark.feature("lcd-screen"),
]


def _write_lock(lock_file: Path, subject: str) -> None:
    lock_file.write_text(f"{subject}\n", encoding="utf-8")


def test_check_lcd_waits_for_daemon_and_clears_lock(tmp_path):
    lock_file = tmp_path / "locks" / "lcd_screen.lck"
    lock_file.parent.mkdir()

    def fake_notify(subject, body=""):
        _write_lock(lock_file, subject)

    def clear_lock():
        # Give the command time to observe the write before removal.
        time.sleep(0.1)
        lock_file.unlink(missing_ok=True)

    clear_thread = threading.Thread(target=clear_lock)
    clear_thread.start()

    with (
        patch.object(settings, "BASE_DIR", tmp_path),
        patch("core.management.commands.check_lcd.notify", side_effect=fake_notify),
    ):
        call_command("check_lcd", "HELLO", "--timeout", "1", "--poll-interval", "0.05")

    clear_thread.join(timeout=1)
    assert not lock_file.exists()


def test_check_lcd_errors_when_lock_not_cleared(tmp_path):
    lock_file = tmp_path / "locks" / "lcd_screen.lck"
    lock_file.parent.mkdir()

    def fake_notify(subject, body=""):
        _write_lock(lock_file, subject)

    with (
        patch.object(settings, "BASE_DIR", tmp_path),
        patch("core.management.commands.check_lcd.notify", side_effect=fake_notify),
    ):
        with pytest.raises(CommandError):
            call_command("check_lcd", "HELLO", "--timeout", "0.1", "--poll-interval", "0.01")

    assert lock_file.exists()
