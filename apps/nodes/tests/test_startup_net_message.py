from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.nodes import tasks


class DummyNode:
    role = SimpleNamespace(name="Controller")

    def get_preferred_scheme(self) -> str:
        return "http"


@pytest.mark.django_db
def test_send_startup_net_message_writes_boot_status(
    monkeypatch, settings, tmp_path
):
    settings.BASE_DIR = tmp_path
    cache.delete(tasks.STARTUP_NET_MESSAGE_CACHE_KEY)

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd_screen_enabled.lck").write_text("", encoding="utf-8")

    started_at = timezone.make_aware(datetime(2024, 1, 1, 0, 0, 0))
    (lock_dir / "suite_uptime.lck").write_text(
        json.dumps({"started_at": started_at.isoformat()}), encoding="utf-8"
    )

    def write_high_lock(*, base_dir, port, lock_file=None):
        target = lock_file or (Path(base_dir) / ".locks" / tasks.LCD_HIGH_LOCK_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hi\nthere\n", encoding="utf-8")
        return target

    monkeypatch.setattr(
        tasks.django_timezone, "now", lambda: started_at + timedelta(seconds=42)
    )
    monkeypatch.setattr(tasks.Node, "get_local", lambda: DummyNode())
    monkeypatch.setattr(tasks, "queue_startup_message", write_high_lock)

    tasks.send_startup_net_message()

    high_lines = (lock_dir / tasks.LCD_HIGH_LOCK_FILE).read_text().splitlines()
    assert high_lines == ["hi", "there"]

    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0] == "UP 0m42s"
    assert low_lines[1] == "Controller"


@pytest.mark.django_db
def test_send_startup_net_message_formats_minutes_and_seconds(
    monkeypatch, settings, tmp_path
):
    settings.BASE_DIR = tmp_path
    cache.delete(tasks.STARTUP_NET_MESSAGE_CACHE_KEY)

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd_screen_enabled.lck").write_text("", encoding="utf-8")

    started_at = timezone.make_aware(datetime(2024, 1, 1, 0, 0, 0))
    (lock_dir / "suite_uptime.lck").write_text(
        json.dumps({"started_at": started_at.isoformat()}), encoding="utf-8"
    )

    def write_high_lock(*, base_dir, port, lock_file=None):
        target = lock_file or (Path(base_dir) / ".locks" / tasks.LCD_HIGH_LOCK_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hi\nthere\n", encoding="utf-8")
        return target

    monkeypatch.setattr(
        tasks.django_timezone, "now", lambda: started_at + timedelta(seconds=125)
    )
    monkeypatch.setattr(tasks.Node, "get_local", lambda: DummyNode())
    monkeypatch.setattr(tasks, "queue_startup_message", write_high_lock)

    tasks.send_startup_net_message()

    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0] == "UP 2m5s"
    assert low_lines[1] == "Controller"
