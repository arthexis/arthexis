from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.nodes import tasks


class DummyCache:
    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    def add(self, key, value, timeout=None):  # noqa: ANN001
        if key in self._store:
            return False
        self._store[key] = value
        return True

    def delete(self, key):  # noqa: ANN001
        self._store.pop(key, None)
        return True


@pytest.fixture
def startup_cache(monkeypatch) -> DummyCache:
    cache = DummyCache()
    monkeypatch.setattr(tasks, "cache", cache)
    return cache


class DummyResponse:
    status_code = 201


class DummyNode:
    role = SimpleNamespace(name="Control", acronym="CTRL")

    def get_preferred_scheme(self) -> str:
        return "http"


@pytest.mark.django_db
def test_send_startup_net_message_writes_boot_status(
    monkeypatch, settings, tmp_path, startup_cache
):
    settings.BASE_DIR = tmp_path
    startup_cache.delete(tasks.STARTUP_NET_MESSAGE_CACHE_KEY)

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
    monkeypatch.setattr(tasks, "_active_interface_label", lambda: "NA")
    monkeypatch.setattr(tasks, "_ap_mode_enabled", lambda: False)
    monkeypatch.setattr(
        tasks.psutil,
        "boot_time",
        lambda: (started_at - timedelta(seconds=30)).timestamp(),
    )

    tasks.send_startup_net_message()

    high_lines = (lock_dir / tasks.LCD_HIGH_LOCK_FILE).read_text().splitlines()
    assert high_lines == ["hi", "there"]

    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0] == "UP 0d0h0m"
    assert low_lines[1] == "ON 0m30s NA"


@pytest.mark.django_db
def test_boot_message_reports_uptime(monkeypatch, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    startup_cache = DummyCache()
    monkeypatch.setattr(tasks, "cache", startup_cache)
    startup_cache.delete(tasks.STARTUP_NET_MESSAGE_CACHE_KEY)

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
        tasks.django_timezone, "now", lambda: started_at + timedelta(seconds=75)
    )
    monkeypatch.setattr(tasks.Node, "get_local", lambda: DummyNode())
    monkeypatch.setattr(tasks, "queue_startup_message", write_high_lock)
    monkeypatch.setattr(tasks, "_active_interface_label", lambda: "NA")
    monkeypatch.setattr(tasks, "_ap_mode_enabled", lambda: False)
    monkeypatch.setattr(
        tasks.psutil, "boot_time", lambda: (started_at - timedelta(minutes=1)).timestamp()
    )

    tasks.send_startup_net_message()

    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0].startswith("UP ")
    assert low_lines[1] == "ON 1m0s NA"


@pytest.mark.django_db
def test_boot_message_uses_system_boot_time(monkeypatch, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    startup_cache = DummyCache()
    monkeypatch.setattr(tasks, "cache", startup_cache)
    startup_cache.delete(tasks.STARTUP_NET_MESSAGE_CACHE_KEY)

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd_screen_enabled.lck").write_text("", encoding="utf-8")
    (lock_dir / "role.lck").write_text("Control", encoding="utf-8")

    now = timezone.make_aware(datetime(2024, 1, 1, 0, 3, 0))
    boot_timestamp = (now - timedelta(minutes=2, seconds=30)).timestamp()

    def write_high_lock(*, base_dir, port, lock_file=None):
        target = lock_file or (Path(base_dir) / ".locks" / tasks.LCD_HIGH_LOCK_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hi\nthere\n", encoding="utf-8")
        return target

    monkeypatch.setattr(tasks.django_timezone, "now", lambda: now)
    monkeypatch.setattr(tasks.psutil, "boot_time", lambda: boot_timestamp)
    monkeypatch.setattr(tasks.Node, "get_local", lambda: DummyNode())
    monkeypatch.setattr(tasks, "queue_startup_message", write_high_lock)
    monkeypatch.setattr(tasks, "_active_interface_label", lambda: "NA")
    monkeypatch.setattr(tasks, "_ap_mode_enabled", lambda: False)

    tasks.send_startup_net_message()

    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0] == "UP 0d0h2m"
    assert low_lines[1] == "ON ?m?s NA"


@pytest.mark.django_db
def test_startup_message_cache_resets_each_boot(
    monkeypatch, settings, tmp_path, startup_cache
):
    settings.BASE_DIR = tmp_path

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd_screen_enabled.lck").write_text("", encoding="utf-8")
    (lock_dir / "role.lck").write_text("Control", encoding="utf-8")

    started_at = timezone.make_aware(datetime(2024, 1, 1, 0, 0, 0))
    (lock_dir / "suite_uptime.lck").write_text(
        json.dumps({"started_at": started_at.isoformat()}), encoding="utf-8"
    )

    high_payloads: list[str] = []

    def write_high_lock(*, base_dir, port, lock_file=None):
        target = lock_file or (Path(base_dir) / ".locks" / tasks.LCD_HIGH_LOCK_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = f"call-{len(high_payloads)}"
        target.write_text(f"{payload}\nbody\n", encoding="utf-8")
        high_payloads.append(payload)
        return target

    now = started_at + timedelta(minutes=5)
    monkeypatch.setattr(tasks.django_timezone, "now", lambda: now)
    monkeypatch.setattr(tasks.Node, "get_local", lambda: DummyNode())
    monkeypatch.setattr(tasks, "queue_startup_message", write_high_lock)
    monkeypatch.setattr(tasks, "_active_interface_label", lambda: "NA")
    monkeypatch.setattr(tasks, "_ap_mode_enabled", lambda: False)

    boot_time = started_at - timedelta(minutes=1)
    monkeypatch.setattr(tasks.psutil, "boot_time", lambda: boot_time.timestamp())

    tasks.send_startup_net_message()
    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0] == "UP 0d0h5m"
    assert high_payloads == ["call-0"]

    (lock_dir / "role.lck").write_text("Terminal", encoding="utf-8")
    boot_time = boot_time + timedelta(hours=1)
    monkeypatch.setattr(tasks.psutil, "boot_time", lambda: boot_time.timestamp())

    tasks.send_startup_net_message()
    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0] == "UP 0d0h5m"
    assert high_payloads == ["call-0", "call-1"]


@pytest.mark.django_db
def test_lcd_boot_message_avoids_database(
    monkeypatch, settings, tmp_path, django_assert_num_queries, startup_cache
):
    settings.BASE_DIR = tmp_path
    startup_cache.delete(tasks.STARTUP_NET_MESSAGE_CACHE_KEY)

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
    monkeypatch.setattr(tasks, "_active_interface_label", lambda: "NA")
    monkeypatch.setattr(tasks, "_ap_mode_enabled", lambda: False)
    monkeypatch.setattr(
        tasks.psutil,
        "boot_time",
        lambda: (started_at - timedelta(seconds=30)).timestamp(),
    )

    with django_assert_num_queries(0):
        tasks.send_startup_net_message()

    low_lines = (lock_dir / tasks.LCD_LOW_LOCK_FILE).read_text().splitlines()
    assert low_lines[0] == "UP 0d0h0m"
    assert low_lines[1] == "ON 0m30s NA"
