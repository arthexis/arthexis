from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.nodes import tasks


class DummyResponse:
    status_code = 201


class DummyNode:
    role = SimpleNamespace(name="Control", acronym="CTRL")

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
    (lock_dir / "role.lck").write_text("Control", encoding="utf-8")

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
    monkeypatch.setattr(tasks, "_active_interface_label", lambda: "n/a")

    tasks.send_startup_net_message()

    high_lines = (lock_dir / tasks.LCD_HIGH_LOCK_FILE).read_text().splitlines()
    assert high_lines == ["hi", "there"]

    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0] == "UP 0d0h0m CTRL"
    assert low_lines[1] == "ON 0h0m n/a"


@pytest.mark.django_db
def test_boot_message_reports_uptime(monkeypatch, settings, tmp_path):
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
        tasks.django_timezone, "now", lambda: started_at + timedelta(seconds=75)
    )
    monkeypatch.setattr(tasks.Node, "get_local", lambda: DummyNode())
    monkeypatch.setattr(tasks, "queue_startup_message", write_high_lock)
    monkeypatch.setattr(tasks, "_active_interface_label", lambda: "n/a")

    tasks.send_startup_net_message()

    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0].startswith("UP ")
    assert low_lines[1] == "ON 0h1m n/a"


@pytest.mark.django_db
def test_lcd_boot_message_avoids_database(
    monkeypatch, settings, tmp_path, django_assert_num_queries
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
    (lock_dir / "role.lck").write_text("Control", encoding="utf-8")

    def write_high_lock(*, base_dir, port, lock_file=None):
        target = lock_file or (Path(base_dir) / ".locks" / tasks.LCD_HIGH_LOCK_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hi\nthere\n", encoding="utf-8")
        return target

    monkeypatch.setattr(
        tasks.django_timezone, "now", lambda: started_at + timedelta(seconds=42)
    )
    monkeypatch.setattr(tasks, "queue_startup_message", write_high_lock)
    monkeypatch.setattr(tasks, "_active_interface_label", lambda: "n/a")

    with django_assert_num_queries(0):
        tasks.send_startup_net_message()

    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0] == "UP 0d0h0m CTRL"
    assert low_lines[1] == "ON 0h0m n/a"
