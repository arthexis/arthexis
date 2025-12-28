from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.nodes import tasks


class DummyResponse:
    status_code = 201


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

    monkeypatch.setattr(
        tasks.django_timezone, "now", lambda: started_at + timedelta(seconds=42)
    )
    monkeypatch.setattr(tasks.Node, "get_local", lambda: DummyNode())
    monkeypatch.setattr(tasks.requests, "get", lambda *_, **__: DummyResponse())

    tasks.send_startup_net_message()

    latest_lines = (lock_dir / tasks.LCD_LATEST_LOCK_FILE).read_text().splitlines()
    assert latest_lines[0] == "BOOT 42s 201"
    assert latest_lines[1] == "Controller"
