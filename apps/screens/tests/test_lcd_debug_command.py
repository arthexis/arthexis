from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings


@pytest.mark.django_db
def test_generates_debug_report(monkeypatch, tmp_path: Path, capsys):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "lcd-high").write_text("hello\nworld\n", encoding="utf-8")
    (lock_dir / "lcd-low").write_text("", encoding="utf-8")
    (lock_dir / "service.lck").write_text("lcd-demo", encoding="utf-8")

    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "lcd-screen.txt").write_text("line1\nline2", encoding="utf-8")

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "lcd-screen.log").write_text("log entry", encoding="utf-8")

    monkeypatch.setenv("LCD_MODE", "demo")
    monkeypatch.setenv("LCD_SKIP_PROBE", "1")

    sleeps: list[int] = []

    def _fake_sleep(self, seconds: int) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        "apps.screens.management.commands.lcd_debug.Command._sleep", _fake_sleep
    )

    with override_settings(BASE_DIR=tmp_path):
        call_command("lcd_debug", "--double")

    report_path = tmp_path / "work" / "lcd-debug.txt"
    assert report_path.exists()

    report = report_path.read_text(encoding="utf-8")
    assert "LCD Debug Report" in report
    assert "LCD timings:" in report
    assert "After 30s" in report
    assert "After 60s" in report
    assert "lcd-high" in report
    assert "lcd-screen.txt" in report
    assert "lcd-screen.log" in report
    assert "Encoding health checks:" in report
    assert "LCD_MODE=demo" in report

    out = capsys.readouterr().out
    assert "Saved LCD debug report" in out
    assert sleeps == [30, 30]
