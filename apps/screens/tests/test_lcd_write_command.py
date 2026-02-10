from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.screens.startup_notifications import (
    LCD_HIGH_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    read_lcd_lock_file,
)


@pytest.fixture()
def temp_base_dir(tmp_path: Path) -> Path:
    (tmp_path / ".locks").mkdir(parents=True, exist_ok=True)
    return tmp_path


def read_lock(base_dir: Path):
    lock_file = base_dir / ".locks" / LCD_LOW_LOCK_FILE
    return read_lcd_lock_file(lock_file)


def test_creates_lock_file_and_sets_values(temp_base_dir: Path):
    with override_settings(BASE_DIR=temp_base_dir):
        call_command(
            "lcd_write",
            subject="Hello",
            body="World",
        )

    lock_payload = read_lock(temp_base_dir)
    assert lock_payload is not None
    assert lock_payload.subject == "Hello"
    assert lock_payload.body == "World"


def test_updates_existing_lock_without_overwriting_missing_fields(temp_base_dir: Path):
    lock_file = temp_base_dir / ".locks" / LCD_LOW_LOCK_FILE
    lock_file.write_text("Original\nBody\n", encoding="utf-8")

    with override_settings(BASE_DIR=temp_base_dir):
        call_command(
            "lcd_write",
            body="Updated",
        )

    lock_payload = read_lock(temp_base_dir)
    assert lock_payload is not None
    assert lock_payload.subject == "Original"
    assert lock_payload.body == "Updated"


def test_delete_lock_file(temp_base_dir: Path):
    lock_file = temp_base_dir / ".locks" / LCD_LOW_LOCK_FILE
    lock_file.write_text("Subject\nBody\n", encoding="utf-8")

    with override_settings(BASE_DIR=temp_base_dir):
        call_command("lcd_write", delete=True)

    assert not lock_file.exists()


def test_restart_reports_failure(temp_base_dir: Path):
    """Surface systemctl stderr when restart exits with a non-zero status."""

    with override_settings(BASE_DIR=temp_base_dir):
        with mock.patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                ["systemctl", "restart", "lcd-demo"],
                returncode=1,
                stdout="",
                stderr="restart failed",
            )

            with pytest.raises(CommandError, match="restart failed"):
                call_command("lcd_write", restart=True, service_name="demo")


def test_restart_handles_missing_systemctl(temp_base_dir: Path):
    """Raise a clear error when systemctl is unavailable on the host."""

    with override_settings(BASE_DIR=temp_base_dir):
        with mock.patch.object(
            subprocess, "run", side_effect=FileNotFoundError
        ) as mock_run:
            with pytest.raises(
                CommandError, match="systemctl not available; cannot restart lcd service"
            ):
                call_command("lcd_write", restart=True, service_name="demo")

    mock_run.assert_called_once_with(
        ["systemctl", "restart", "lcd-demo"], capture_output=True, text=True
    )


@pytest.mark.django_db
@pytest.mark.sigil_roots
@pytest.mark.parametrize(
    ("resolve_sigils", "expected_subject"),
    [
        pytest.param(True, "Resolved", id="resolve-default"),
        pytest.param(False, "[ENV.LCD_SUBJECT]", id="resolve-disabled"),
    ],
)
def test_lcd_write_sigil_resolution_modes(
    monkeypatch,
    temp_base_dir: Path,
    resolve_sigils: bool,
    expected_subject: str,
):
    """Verify sigil resolution can be enabled or bypassed for lcd_write payloads."""

    monkeypatch.setenv("LCD_SUBJECT", "Resolved")

    with override_settings(BASE_DIR=temp_base_dir):
        call_command(
            "lcd_write",
            subject="[ENV.LCD_SUBJECT]",
            body="Body",
            resolve_sigils=resolve_sigils,
        )

    lock_payload = read_lock(temp_base_dir)
    assert lock_payload is not None
    assert lock_payload.subject == expected_subject
    assert lock_payload.body == "Body"
