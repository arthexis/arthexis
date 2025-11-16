from datetime import datetime, timedelta

import pytest

from core import lcd_screen


class DummyResult:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_suite_down_notice_detects_inactive_service(monkeypatch, tmp_path):
    service_lock = tmp_path / "service.lck"
    service_lock.write_text("demo\n", encoding="utf-8")
    monkeypatch.setattr(lcd_screen, "SERVICE_LOCK_FILE", service_lock)
    monkeypatch.setattr(lcd_screen.shutil, "which", lambda cmd: "/bin/systemctl")
    monkeypatch.setattr(
        lcd_screen.subprocess,
        "run",
        lambda *args, **kwargs: DummyResult("inactive\n"),
    )

    notice = lcd_screen._suite_down_notice()

    assert notice == ("demo offline", "Status: Inactive")


def test_suite_down_notice_ignores_active_service(monkeypatch, tmp_path):
    service_lock = tmp_path / "service.lck"
    service_lock.write_text("demo\n", encoding="utf-8")
    monkeypatch.setattr(lcd_screen, "SERVICE_LOCK_FILE", service_lock)
    monkeypatch.setattr(lcd_screen.shutil, "which", lambda cmd: "/bin/systemctl")
    monkeypatch.setattr(
        lcd_screen.subprocess,
        "run",
        lambda *args, **kwargs: DummyResult("active\n"),
    )

    assert lcd_screen._suite_down_notice() is None


def test_system_shutdown_notice_reads_schedule(monkeypatch, tmp_path):
    schedule_file = tmp_path / "scheduled"
    future = datetime.now() + timedelta(hours=1)
    usec_value = int(future.timestamp() * 1_000_000)
    schedule_file.write_text(
        f"USEC={usec_value}\nMODE=reboot\nUNIT=reboot.target\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lcd_screen, "SHUTDOWN_SCHEDULE_FILE", schedule_file)

    notice = lcd_screen._system_shutdown_notice()

    assert notice is not None
    assert notice[0] == "System reboot"
    assert notice[1].startswith("ETA ")


def test_system_shutdown_notice_handles_missing_file(monkeypatch):
    missing_file = lcd_screen.SHUTDOWN_SCHEDULE_FILE.parent / "does-not-exist"
    monkeypatch.setattr(lcd_screen, "SHUTDOWN_SCHEDULE_FILE", missing_file)

    assert lcd_screen._system_shutdown_notice() is None


def test_resolve_payload_prioritizes_shutdown(monkeypatch):
    monkeypatch.setattr(lcd_screen, "_system_shutdown_notice", lambda: ("sys", "down"))
    monkeypatch.setattr(lcd_screen, "_suite_down_notice", lambda: ("suite", "down"))

    payload = lcd_screen._resolve_display_payload(("lock", "file", 500))

    assert payload == ("sys", "down", lcd_screen.DEFAULT_SCROLL_MS, "system-shutdown")


def test_resolve_payload_falls_back_to_suite(monkeypatch):
    monkeypatch.setattr(lcd_screen, "_system_shutdown_notice", lambda: None)
    monkeypatch.setattr(lcd_screen, "_suite_down_notice", lambda: ("suite", "down"))

    payload = lcd_screen._resolve_display_payload(("lock", "file", 500))

    assert payload == ("suite", "down", lcd_screen.DEFAULT_SCROLL_MS, "suite-down")


def test_resolve_payload_uses_lock_when_no_alerts(monkeypatch):
    monkeypatch.setattr(lcd_screen, "_system_shutdown_notice", lambda: None)
    monkeypatch.setattr(lcd_screen, "_suite_down_notice", lambda: None)

    payload = lcd_screen._resolve_display_payload(("lock", "file", 500))

    assert payload == ("lock", "file", 500, "lock-file")
