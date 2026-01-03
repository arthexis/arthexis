from __future__ import annotations

from pathlib import Path

import pytest

from apps.core import notifications


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("ARTHEXIS_BASE_DIR", raising=False)


def test_notify_uses_numbered_channel(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(tmp_path))
    manager = notifications.NotificationManager()

    manager.send("hi", "there", channel_type="high", channel_number=2)

    target = tmp_path / ".locks" / "lcd-high-2.lck"
    assert target.exists()
    assert target.read_text(encoding="utf-8").splitlines() == ["hi", "there"]


def test_notify_supports_all_channel(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(tmp_path))
    manager = notifications.NotificationManager()

    manager.send("hello", "world", channel_type="both")

    target = tmp_path / ".locks" / "lcd-all.lck"
    assert target.exists()
    assert "hello" in target.read_text(encoding="utf-8")
