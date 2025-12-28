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


def test_restart_uses_service_lock(temp_base_dir: Path):
    (temp_base_dir / ".locks" / "service.lck").write_text("demo", encoding="utf-8")

    with override_settings(BASE_DIR=temp_base_dir):
        with mock.patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                ["systemctl", "restart", "lcd-demo"], returncode=0, stdout="", stderr=""
            )
            call_command("lcd_write", restart=True)

    mock_run.assert_called_once_with(
        ["systemctl", "restart", "lcd-demo"], capture_output=True, text=True
    )


def test_restart_reports_failure(temp_base_dir: Path):
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
def test_resolves_sigils_by_default(monkeypatch, temp_base_dir: Path):
    monkeypatch.setenv("LCD_SUBJECT", "Resolved")

    with override_settings(BASE_DIR=temp_base_dir):
        call_command(
            "lcd_write",
            subject="[ENV.LCD_SUBJECT]",
            body="Body",
        )

    lock_payload = read_lock(temp_base_dir)
    assert lock_payload is not None
    assert lock_payload.subject == "Resolved"
    assert lock_payload.body == "Body"


@pytest.mark.django_db
def test_disables_resolving_sigils_when_requested(monkeypatch, temp_base_dir: Path):
    monkeypatch.setenv("LCD_SUBJECT", "Resolved")

    with override_settings(BASE_DIR=temp_base_dir):
        call_command(
            "lcd_write",
            subject="[ENV.LCD_SUBJECT]",
            body="Body",
            resolve_sigils=False,
        )

    lock_payload = read_lock(temp_base_dir)
    assert lock_payload is not None
    assert lock_payload.subject == "[ENV.LCD_SUBJECT]"
    assert lock_payload.body == "Body"
