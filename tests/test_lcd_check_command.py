import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.core.management import call_command  # noqa: E402
from django.conf import settings  # noqa: E402


pytestmark = [
    pytest.mark.role("Terminal"),
    pytest.mark.role("Control"),
    pytest.mark.feature("lcd-screen"),
]


def _create_lock_files(tmp_path):
    locks = tmp_path / "locks"
    locks.mkdir()
    (locks / "lcd_screen.lck").write_text("hello\n", encoding="utf-8")
    (locks / "service.lck").write_text("demo", encoding="utf-8")
    return locks


def _assert_systemctl_probe(mock_run, service_name="demo"):
    mock_run.assert_called_once_with(
        ["systemctl", "is-active", f"lcd-{service_name}"],
        capture_output=True,
        text=True,
    )


def test_lcd_check_sends_random_string(tmp_path):
    _create_lock_files(tmp_path)

    mock_run = Mock(return_value=SimpleNamespace(returncode=0, stdout="active\n"))

    with (
        patch("core.management.commands.lcd_check.CharLCD1602") as mock_lcd,
        patch("core.management.commands.lcd_check.notify") as mock_notify,
        patch("builtins.input", return_value="y"),
        patch("random.choices", return_value=list("ABC123")),
        patch("subprocess.run", mock_run),
        patch.object(settings, "BASE_DIR", tmp_path),
    ):
        call_command("lcd_check")

    _assert_systemctl_probe(mock_run)

    mock_lcd.assert_called_once()
    mock_lcd.return_value.init_lcd.assert_called_once()
    mock_notify.assert_called_once_with(subject="ABC123")


def test_lcd_check_advises_when_i2c_missing(tmp_path, capsys):
    _create_lock_files(tmp_path)

    mock_run = Mock(return_value=SimpleNamespace(returncode=0, stdout="active\n"))

    with (
        patch(
            "core.management.commands.lcd_check.CharLCD1602",
            side_effect=FileNotFoundError(2, "No such file or directory", "/dev/i2c-1"),
        ),
        patch("core.management.commands.lcd_check.notify"),
        patch("builtins.input", return_value="n"),
        patch("random.choices", return_value=list("ABC123")),
        patch("subprocess.run", mock_run),
        patch.object(settings, "BASE_DIR", tmp_path),
    ):
        call_command("lcd_check")

    _assert_systemctl_probe(mock_run)

    out = capsys.readouterr().out
    assert "Unexpected error during LCD init" in out
    assert "enable the I2C interface" in out
    assert "raspi-config" in out
