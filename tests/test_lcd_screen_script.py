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

    payload = lcd_screen._resolve_display_payload(
        lcd_screen.LockPayload("lock", "file", 500, False)
    )

    assert payload == ("sys", "down", lcd_screen.DEFAULT_SCROLL_MS, "system-shutdown")


def test_resolve_payload_falls_back_to_suite(monkeypatch):
    monkeypatch.setattr(lcd_screen, "_system_shutdown_notice", lambda: None)
    monkeypatch.setattr(lcd_screen, "_suite_down_notice", lambda: ("suite", "down"))

    payload = lcd_screen._resolve_display_payload(
        lcd_screen.LockPayload("lock", "file", 500, False)
    )

    assert payload == ("suite", "down", lcd_screen.DEFAULT_SCROLL_MS, "suite-down")


def test_resolve_payload_uses_lock_when_no_alerts(monkeypatch):
    monkeypatch.setattr(lcd_screen, "_system_shutdown_notice", lambda: None)
    monkeypatch.setattr(lcd_screen, "_suite_down_notice", lambda: None)

    payload = lcd_screen._resolve_display_payload(
        lcd_screen.LockPayload("lock", "file", 500, False)
    )

    assert payload == ("lock", "file", 500, "lock-file")


def test_lock_file_matches_detects_updated_payload(monkeypatch, tmp_path):
    lock_file = tmp_path / "locks" / "lcd_screen.lck"
    lock_file.parent.mkdir(parents=True)
    monkeypatch.setattr(lcd_screen, "LOCK_FILE", lock_file)

    payload = lcd_screen.LockPayload("hello", "world", 750, False)
    lock_file.write_text("hello\nworld\n750\n", encoding="utf-8")
    original_mtime = lock_file.stat().st_mtime

    assert lcd_screen._lock_file_matches(payload, original_mtime)

    lock_file.write_text("new\nmessage\n", encoding="utf-8")

    assert not lcd_screen._lock_file_matches(payload, original_mtime)


def test_read_lock_file_sets_net_message_flag(monkeypatch, tmp_path):
    lock_file = tmp_path / "locks" / "lcd_screen.lck"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("hello\nworld\nnet-message\n500\n", encoding="utf-8")
    monkeypatch.setattr(lcd_screen, "LOCK_FILE", lock_file)

    payload = lcd_screen._read_lock_file()

    assert payload == lcd_screen.LockPayload("hello", "world", 500, True)


def test_net_message_broadcasts_once(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        lcd_screen,
        "_broadcast_net_message",
        lambda subject, body: calls.append((subject, body)) or True,
    )

    payload = lcd_screen.LockPayload("hello", "world", 1000, True)

    last_sent = lcd_screen._send_net_message_from_lock(payload, None)
    assert calls == [("hello", "world")]

    repeat_sent = lcd_screen._send_net_message_from_lock(payload, last_sent)
    assert calls == [("hello", "world")]
    assert repeat_sent == ("hello", "world")


def test_blank_display_clears_screen():
    calls: list[tuple[str, str | int]] = []

    class FakeLCD:
        def clear(self):
            calls.append(("clear", ""))

        def write(self, x: int, y: int, text: str):
            calls.append(("write", x, y, text))

    lcd = FakeLCD()

    lcd_screen._blank_display(lcd)

    assert ("clear", "") in calls
    assert ("write", 0, 0, " " * 16) in calls
    assert ("write", 0, 1, " " * 16) in calls


def test_handle_shutdown_request_blanks_display():
    lcd_screen._reset_shutdown_flag()
    lcd_screen._request_shutdown(None, None)

    calls: list[str] = []

    class FakeLCD:
        def clear(self):
            calls.append("clear")

        def write(self, x: int, y: int, text: str):
            calls.append("write")

    lcd = FakeLCD()

    assert lcd_screen._handle_shutdown_request(lcd) is True
    assert "clear" in calls
    assert "write" in calls

    lcd_screen._reset_shutdown_flag()


def test_handle_shutdown_request_noop_without_signal():
    lcd_screen._reset_shutdown_flag()

    assert lcd_screen._handle_shutdown_request(None) is False


def test_display_breaks_on_shutdown(monkeypatch):
    lcd_screen._reset_shutdown_flag()

    writes: list[tuple[int, int, str]] = []

    class FakeLCD:
        def write(self, x: int, y: int, text: str):
            writes.append((x, y, text))
            if len(writes) == 2:
                lcd_screen._request_shutdown(None, None)

    lcd = FakeLCD()
    state = lcd_screen._prepare_display_state("a" * 64, "b" * 64, 1000)

    state = lcd_screen._advance_display(lcd, state)
    state = lcd_screen._advance_display(lcd, state)

    assert len(writes) == 2


def test_display_loops_segments(monkeypatch):
    lcd_screen._reset_shutdown_flag()

    cycles: list[tuple[str, str]] = []

    class FakeLCD:
        def __init__(self):
            self.buffer: list[str] = []

        def write(self, x: int, y: int, text: str):
            self.buffer.append(text)
            if len(self.buffer) == 2:
                line1, line2 = self.buffer
                cycles.append((line1, line2))
                self.buffer.clear()

    lcd = FakeLCD()
    state = lcd_screen._prepare_display_state(
        "SCROLLING MESSAGE!", "SECOND LINE OF TEXT", 200
    )

    for _ in range(state.cycle + 1):
        state = lcd_screen._advance_display(lcd, state)

    assert cycles[0] == cycles[-1]
