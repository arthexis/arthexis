from __future__ import annotations

import json
import signal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.screens.management.commands.lcd_actions import write
from apps.screens.startup_notifications import (
    LCD_HIGH_LOCK_FILE,
    read_lcd_lock_file,
    render_lcd_lock_file,
)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def _prepare_base_dir(tmp_path):
    (tmp_path / "manage.py").write_text("# test manage.py\n", encoding="utf-8")
    return tmp_path


def _repeater_command_line(
    base_dir, *, subject: str, body: str, token: str | None = None
) -> str:
    parts = [
        "python",
        str(base_dir / "manage.py"),
        "lcd",
        "write",
        "--run-important-repeater",
        f"--subject={subject}",
        f"--body={body}",
    ]
    if token:
        parts += [f"--repeater-token={token}"]
    return " ".join(parts)


def test_lcd_write_important_starts_singleton_repeater(monkeypatch, tmp_path):
    base_dir = _prepare_base_dir(tmp_path)
    popen_calls: list[tuple[list[str], dict]] = []

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return FakeProcess(4321)

    monkeypatch.setattr(write.subprocess, "Popen", fake_popen)

    stdout = StringIO()
    with override_settings(BASE_DIR=base_dir):
        call_command(
            "lcd",
            "write",
            "--subject",
            "DONE",
            "--body",
            "MOVE CARD",
            "--important",
            stdout=stdout,
        )

    state = json.loads(
        (base_dir / ".locks" / write.IMPORTANT_REPEATER_STATE_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert state["pid"] == 4321
    assert state["subject"] == "DONE"
    assert state["body"] == "MOVE CARD"
    assert state["display_seconds"] == 60.0
    assert state["repeat_seconds"] == 180.0
    assert state["refresh_seconds"] == 15.0
    assert len(state["token"]) > 10

    command, kwargs = popen_calls[0]
    assert command[1] == str(base_dir / "manage.py")
    assert "--run-important-repeater" in command
    assert "--no-resolve" in command
    assert "--subject=DONE" in command
    assert "--body=MOVE CARD" in command
    assert f"--repeater-token={state['token']}" in command
    assert kwargs["cwd"] == base_dir
    assert kwargs["start_new_session"] is True
    assert "Started important LCD repeater 4321" in stdout.getvalue()


def test_lcd_write_important_ignores_existing_high_lock_expiry(monkeypatch, tmp_path):
    base_dir = _prepare_base_dir(tmp_path)
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()
    high_lock = lock_dir / LCD_HIGH_LOCK_FILE
    high_lock.write_text(
        render_lcd_lock_file(
            subject="OLD",
            body="EXPIRING",
            expires_at="2030-01-01T00:00:00+00:00",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        write.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(222),
    )

    with override_settings(BASE_DIR=base_dir):
        call_command(
            "lcd",
            "write",
            "--subject",
            "NEW",
            "--body",
            "NOTICE",
            "--important",
        )

    state = json.loads(
        (lock_dir / write.IMPORTANT_REPEATER_STATE_NAME).read_text(encoding="utf-8")
    )
    assert state["subject"] == "NEW"
    assert state["body"] == "NOTICE"


def test_lcd_write_important_stores_rendered_payload(monkeypatch, tmp_path):
    base_dir = _prepare_base_dir(tmp_path)
    popen_calls: list[tuple[list[str], dict]] = []
    raw_subject = f"  {'A' * 70}  "
    raw_body = f"  {'B' * 70}  "

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return FakeProcess(333)

    monkeypatch.setattr(write.subprocess, "Popen", fake_popen)

    with override_settings(BASE_DIR=base_dir):
        call_command(
            "lcd",
            "write",
            "--subject",
            raw_subject,
            "--body",
            raw_body,
            "--important",
        )

    state = json.loads(
        (base_dir / ".locks" / write.IMPORTANT_REPEATER_STATE_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert state["subject"] == "A" * 64
    assert state["body"] == "B" * 64
    command, _kwargs = popen_calls[0]
    assert f"--subject={'A' * 64}" in command
    assert f"--body={'B' * 64}" in command


def test_lcd_write_important_passes_dash_prefixed_payloads_as_option_values(
    monkeypatch, tmp_path
):
    base_dir = _prepare_base_dir(tmp_path)
    popen_calls: list[tuple[list[str], dict]] = []

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return FakeProcess(444)

    monkeypatch.setattr(write.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(write.secrets, "token_urlsafe", lambda size: "-dash-token")

    with override_settings(BASE_DIR=base_dir):
        call_command(
            "lcd",
            "write",
            "--subject=-DONE",
            "--body=-MOVE CARD",
            "--important",
        )

    state = json.loads(
        (base_dir / ".locks" / write.IMPORTANT_REPEATER_STATE_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert state["subject"] == "-DONE"
    assert state["body"] == "-MOVE CARD"
    command, _kwargs = popen_calls[0]
    assert "--subject=-DONE" in command
    assert "--body=-MOVE CARD" in command
    assert "--repeater-token=-dash-token" in command
    assert "--subject" not in command
    assert "--body" not in command
    assert "--repeater-token" not in command


def test_lcd_write_important_replaces_existing_repeater_without_clearing_lock(
    monkeypatch, tmp_path
):
    base_dir = _prepare_base_dir(tmp_path)
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()
    (lock_dir / write.IMPORTANT_REPEATER_STATE_NAME).write_text(
        json.dumps(
            {"pid": 111, "subject": "OLD", "body": "MESSAGE", "token": "old-token"}
        ),
        encoding="utf-8",
    )
    high_lock = lock_dir / LCD_HIGH_LOCK_FILE
    high_lock.write_text(
        render_lcd_lock_file(subject="OLD", body="MESSAGE"),
        encoding="utf-8",
    )
    kills: list[tuple[int, int]] = []
    alive = {"value": True}

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        if sig == signal.SIGTERM:
            alive["value"] = False

    monkeypatch.setattr(write.os, "kill", fake_kill)
    monkeypatch.setattr(write, "_pid_alive", lambda pid: alive["value"])
    monkeypatch.setattr(
        write,
        "_read_process_command_line",
        lambda pid: _repeater_command_line(
            base_dir, subject="OLD", body="MESSAGE", token="old-token"
        ),
    )
    monkeypatch.setattr(
        write.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(222),
    )

    with override_settings(BASE_DIR=base_dir):
        call_command(
            "lcd",
            "write",
            "--subject",
            "NEW",
            "--body",
            "NOTICE",
            "--important",
        )

    state = json.loads(
        (lock_dir / write.IMPORTANT_REPEATER_STATE_NAME).read_text(encoding="utf-8")
    )
    assert state["pid"] == 222
    assert state["subject"] == "NEW"
    assert read_lcd_lock_file(high_lock).subject == "OLD"
    assert (111, signal.SIGTERM) in kills


def test_lcd_write_stop_important_stops_repeater_and_clears_owned_lock(
    monkeypatch, tmp_path
):
    base_dir = _prepare_base_dir(tmp_path)
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()
    state_path = lock_dir / write.IMPORTANT_REPEATER_STATE_NAME
    state_path.write_text(
        json.dumps(
            {"pid": 777, "subject": "DONE", "body": "MOVE CARD", "token": "done-token"}
        ),
        encoding="utf-8",
    )
    high_lock = lock_dir / LCD_HIGH_LOCK_FILE
    high_lock.write_text(
        render_lcd_lock_file(subject="DONE", body="MOVE CARD"),
        encoding="utf-8",
    )
    kills: list[tuple[int, int]] = []
    alive = {"value": True}

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        if sig == signal.SIGTERM:
            alive["value"] = False

    monkeypatch.setattr(write.os, "kill", fake_kill)
    monkeypatch.setattr(write, "_pid_alive", lambda pid: alive["value"])
    monkeypatch.setattr(
        write,
        "_read_process_command_line",
        lambda pid: _repeater_command_line(
            base_dir, subject="DONE", body="MOVE CARD", token="done-token"
        ),
    )

    stdout = StringIO()
    with override_settings(BASE_DIR=base_dir):
        call_command("lcd", "write", "--stop-important", stdout=stdout)

    assert not state_path.exists()
    assert not high_lock.exists()
    assert (777, signal.SIGTERM) in kills
    assert "Stopped important LCD repeater: 777" in stdout.getvalue()


def test_lcd_write_stop_important_does_not_signal_mismatched_pid(monkeypatch, tmp_path):
    base_dir = _prepare_base_dir(tmp_path)
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()
    state_path = lock_dir / write.IMPORTANT_REPEATER_STATE_NAME
    state_path.write_text(
        json.dumps(
            {"pid": 888, "subject": "DONE", "body": "MOVE CARD", "token": "owned"}
        ),
        encoding="utf-8",
    )
    high_lock = lock_dir / LCD_HIGH_LOCK_FILE
    high_lock.write_text(
        render_lcd_lock_file(subject="DONE", body="MOVE CARD"),
        encoding="utf-8",
    )
    kills: list[tuple[int, int]] = []

    monkeypatch.setattr(write.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(write, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        write,
        "_read_process_command_line",
        lambda pid: _repeater_command_line(
            base_dir, subject="DONE", body="MOVE CARD", token="different"
        ),
    )

    stdout = StringIO()
    with override_settings(BASE_DIR=base_dir):
        call_command("lcd", "write", "--stop-important", stdout=stdout)

    assert kills == []
    assert not state_path.exists()
    assert not high_lock.exists()
    assert "Stopped important LCD repeater: (none)" in stdout.getvalue()


def test_lcd_write_stop_important_preserves_state_when_pid_cannot_be_verified(
    monkeypatch, tmp_path
):
    base_dir = _prepare_base_dir(tmp_path)
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()
    state_path = lock_dir / write.IMPORTANT_REPEATER_STATE_NAME
    state_path.write_text(
        json.dumps(
            {"pid": 889, "subject": "DONE", "body": "MOVE CARD", "token": "owned"}
        ),
        encoding="utf-8",
    )
    high_lock = lock_dir / LCD_HIGH_LOCK_FILE
    high_lock.write_text(
        render_lcd_lock_file(subject="DONE", body="MOVE CARD"),
        encoding="utf-8",
    )

    monkeypatch.setattr(write, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(write, "_read_process_command_line", lambda pid: None)
    monkeypatch.setattr(
        write.os,
        "kill",
        lambda pid, sig: pytest.fail("unverified PID should not be signaled"),
    )

    with override_settings(BASE_DIR=base_dir):
        with pytest.raises(CommandError, match="ownership could not be verified"):
            call_command("lcd", "write", "--stop-important")

    assert state_path.exists()
    assert high_lock.exists()


def test_lcd_write_stop_important_clears_rendered_owned_lock(monkeypatch, tmp_path):
    base_dir = _prepare_base_dir(tmp_path)
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()
    raw_subject = f"  {'A' * 70}  "
    raw_body = f"  {'B' * 70}  "
    state_path = lock_dir / write.IMPORTANT_REPEATER_STATE_NAME
    state_path.write_text(
        json.dumps(
            {"pid": 999, "subject": raw_subject, "body": raw_body, "token": "rendered"}
        ),
        encoding="utf-8",
    )
    high_lock = lock_dir / LCD_HIGH_LOCK_FILE
    high_lock.write_text(
        render_lcd_lock_file(subject=raw_subject, body=raw_body),
        encoding="utf-8",
    )
    alive = {"value": True}

    def fake_kill(pid, sig):
        if sig == signal.SIGTERM:
            alive["value"] = False

    monkeypatch.setattr(write.os, "kill", fake_kill)
    monkeypatch.setattr(write, "_pid_alive", lambda pid: alive["value"])
    monkeypatch.setattr(
        write,
        "_read_process_command_line",
        lambda pid: _repeater_command_line(
            base_dir, subject=raw_subject, body=raw_body, token="rendered"
        ),
    )

    with override_settings(BASE_DIR=base_dir):
        call_command("lcd", "write", "--stop-important")

    assert not high_lock.exists()


def test_lcd_write_important_requires_minimum_display_window(tmp_path):
    base_dir = _prepare_base_dir(tmp_path)

    with override_settings(BASE_DIR=base_dir):
        with pytest.raises(CommandError, match="--display-seconds"):
            call_command(
                "lcd",
                "write",
                "--subject",
                "DONE",
                "--body",
                "MOVE CARD",
                "--important",
                "--display-seconds",
                "30",
            )
