from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.screens.startup_notifications import (
    LCD_LOCK_FILE,
    LCD_STATE_DISABLED,
    LCD_STATE_ENABLED,
    read_lcd_lock_file,
)


@pytest.fixture()
def temp_base_dir(tmp_path: Path) -> Path:
    (tmp_path / ".locks").mkdir(parents=True, exist_ok=True)
    return tmp_path


def read_lock(base_dir: Path):
    lock_file = base_dir / ".locks" / LCD_LOCK_FILE
    return read_lcd_lock_file(lock_file)


def test_creates_lock_file_and_sets_values(temp_base_dir: Path):
    with override_settings(BASE_DIR=temp_base_dir):
        call_command(
            "lcd_write",
            state=LCD_STATE_ENABLED,
            subject="Hello",
            body="World",
            flag=["net-message", "scroll_ms=1500"],
            clear_flags=True,
        )

    lock_payload = read_lock(temp_base_dir)
    assert lock_payload is not None
    assert lock_payload.state == LCD_STATE_ENABLED
    assert lock_payload.subject == "Hello"
    assert lock_payload.body == "World"
    assert lock_payload.flags == ("net-message", "scroll_ms=1500")


def test_updates_existing_lock_without_overwriting_missing_fields(temp_base_dir: Path):
    lock_file = temp_base_dir / ".locks" / LCD_LOCK_FILE
    lock_file.write_text(
        "\n".join(
            [
                "state=disabled",
                "Original",
                "Body",
                "scroll_ms=500",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with override_settings(BASE_DIR=temp_base_dir):
        call_command(
            "lcd_write",
            body="Updated",
            flag=["net-message"],
        )

    lock_payload = read_lock(temp_base_dir)
    assert lock_payload is not None
    assert lock_payload.state == LCD_STATE_DISABLED
    assert lock_payload.subject == "Original"
    assert lock_payload.body == "Updated"
    assert lock_payload.flags == ("scroll_ms=500", "net-message")


def test_delete_lock_file(temp_base_dir: Path):
    lock_file = temp_base_dir / ".locks" / LCD_LOCK_FILE
    lock_file.write_text("state=enabled\nSubject\nBody\n", encoding="utf-8")

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
