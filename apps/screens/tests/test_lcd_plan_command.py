from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.screens.management.commands.lcd_plan import Command


@pytest.fixture()
def temp_base_dir(tmp_path: Path) -> Path:
    (tmp_path / ".locks").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_lcd_plan_includes_high_and_low(monkeypatch, temp_base_dir: Path) -> None:
    (temp_base_dir / ".locks" / "lcd-high").write_text("HIGH\nMSG\n", encoding="utf-8")
    (temp_base_dir / ".locks" / "lcd-low").write_text("LOW\nNOTE\n", encoding="utf-8")
    fixed_now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(Command, "_now", lambda self: fixed_now)

    out = StringIO()
    with override_settings(BASE_DIR=temp_base_dir):
        call_command("lcd_plan", seconds=12, stdout=out)

    output = out.getvalue()
    assert "[high]" in output
    assert "[low]" in output
    assert "HIGH" in output
    assert "LOW" in output


def test_lcd_plan_prioritizes_event(monkeypatch, temp_base_dir: Path) -> None:
    (temp_base_dir / ".locks" / "lcd-high").write_text("HIGH\nMSG\n", encoding="utf-8")
    (temp_base_dir / ".locks" / "lcd-event-1.lck").write_text(
        "EVENT\nNOW\n5\n", encoding="utf-8"
    )
    fixed_now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(Command, "_now", lambda self: fixed_now)

    out = StringIO()
    with override_settings(BASE_DIR=temp_base_dir):
        call_command("lcd_plan", seconds=6, stdout=out)

    output = out.getvalue()
    assert "[event]" in output
    assert "EVENT" in output
