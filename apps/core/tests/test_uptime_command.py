from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.core.management.commands import uptime as uptime_command


def _write_lock(lock_dir: Path, started_at: datetime) -> Path:
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / "suite_uptime.lck"
    path.write_text(json.dumps({"started_at": started_at.isoformat()}), encoding="utf-8")
    return path


@pytest.mark.django_db
def test_uptime_command_reports_lcd_format(monkeypatch, settings, tmp_path):
    now = timezone.make_aware(datetime(2024, 1, 1, 12, 0, 0))
    started_at = now - timedelta(hours=2, minutes=5)

    settings.BASE_DIR = tmp_path
    lock_path = _write_lock(tmp_path / ".locks", started_at)
    os.utime(lock_path, (now.timestamp(), now.timestamp()))

    monkeypatch.setattr(uptime_command.timezone, "now", lambda: now)
    monkeypatch.setattr(uptime_command.node_tasks.django_timezone, "now", lambda: now)
    monkeypatch.setattr(uptime_command.system, "_system_boot_time", lambda _now: started_at - timedelta(minutes=1))
    monkeypatch.setattr(
        uptime_command.node_tasks.psutil,
        "boot_time",
        lambda: (started_at - timedelta(minutes=1)).timestamp(),
    )
    monkeypatch.setattr(
        uptime_command.node_tasks, "_active_interface_label", lambda: "NA"
    )
    monkeypatch.setattr(uptime_command.node_tasks, "_ap_mode_enabled", lambda: False)

    stdout = io.StringIO()
    call_command("uptime", stdout=stdout)

    output = stdout.getvalue()
    assert "UP 0d2h5m" in output
    assert "ON 1m0s NA" in output
    assert "Uptime lock status: OK" in output


@pytest.mark.django_db
def test_uptime_command_warns_when_lock_missing(monkeypatch, settings, tmp_path):
    now = timezone.make_aware(datetime(2024, 1, 1, 12, 0, 0))

    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(uptime_command.timezone, "now", lambda: now)
    monkeypatch.setattr(uptime_command.node_tasks.django_timezone, "now", lambda: now)
    monkeypatch.setattr(uptime_command.system, "_system_boot_time", lambda _now: now - timedelta(hours=1))

    stdout = io.StringIO()
    call_command("uptime", stdout=stdout)

    output = stdout.getvalue()
    assert "Suite uptime lock missing" in output


@pytest.mark.django_db
def test_uptime_command_detects_stale_heartbeat(monkeypatch, settings, tmp_path):
    now = timezone.make_aware(datetime(2024, 1, 1, 12, 0, 0))
    started_at = now - timedelta(hours=1)
    stale_time = now - (uptime_command.system.SUITE_UPTIME_LOCK_MAX_AGE * 2)

    settings.BASE_DIR = tmp_path
    lock_path = _write_lock(tmp_path / ".locks", started_at)
    os.utime(lock_path, (stale_time.timestamp(), stale_time.timestamp()))

    monkeypatch.setattr(uptime_command.timezone, "now", lambda: now)
    monkeypatch.setattr(uptime_command.node_tasks.django_timezone, "now", lambda: now)
    monkeypatch.setattr(uptime_command.system, "_system_boot_time", lambda _now: now - timedelta(hours=2))

    stdout = io.StringIO()
    call_command("uptime", stdout=stdout)

    output = stdout.getvalue()
    assert "heartbeat is stale" in output
