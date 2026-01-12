from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings


class FakeLCD:
    def __init__(self, *args, **kwargs) -> None:
        self.timings = None

    def init_lcd(self, *args, **kwargs) -> None:
        return None

    def reset(self) -> None:
        return None

    def clear(self) -> None:
        return None

    def write(self, *args, **kwargs) -> None:
        return None


@pytest.fixture()
def temp_base_dir(tmp_path: Path) -> Path:
    (tmp_path / ".locks").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_calibrate_saves_lock_file(temp_base_dir: Path):
    (temp_base_dir / ".locks" / "service.lck").write_text("demo", encoding="utf-8")

    inputs = ["", "", "", "", "", "y"]
    with (
        override_settings(BASE_DIR=temp_base_dir),
        mock.patch("builtins.input", side_effect=inputs),
        mock.patch(
            "apps.screens.management.commands.lcd_calibrate.prepare_lcd_controller",
            return_value=FakeLCD(),
        ),
        mock.patch.object(subprocess, "run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            ["systemctl", "stop", "lcd-demo"], returncode=0, stdout="", stderr=""
        )
        call_command("lcd_calibrate")

    lock_file = temp_base_dir / ".locks" / "lcd-timings"
    assert lock_file.exists()
    content = lock_file.read_text(encoding="utf-8")
    assert "pulse_enable_delay=" in content


def test_calibrate_can_skip_save(temp_base_dir: Path):
    inputs = ["", "", "", "", "", "n"]
    with (
        override_settings(BASE_DIR=temp_base_dir),
        mock.patch("builtins.input", side_effect=inputs),
        mock.patch(
            "apps.screens.management.commands.lcd_calibrate.prepare_lcd_controller",
            return_value=FakeLCD(),
        ),
    ):
        call_command("lcd_calibrate")

    lock_file = temp_base_dir / ".locks" / "lcd-timings"
    assert not lock_file.exists()


def test_restart_requires_service_name(temp_base_dir: Path):
    inputs = ["", "", "", "", "", "n"]
    with (
        override_settings(BASE_DIR=temp_base_dir),
        mock.patch("builtins.input", side_effect=inputs),
        mock.patch(
            "apps.screens.management.commands.lcd_calibrate.prepare_lcd_controller",
            return_value=FakeLCD(),
        ),
        pytest.raises(CommandError, match="Service name is required"),
    ):
        call_command("lcd_calibrate", restart=True)
