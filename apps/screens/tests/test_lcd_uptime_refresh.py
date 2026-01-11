import json
import os
from datetime import datetime, timedelta, timezone

from apps.screens import lcd_screen


def test_refresh_uptime_payload_updates_subject(tmp_path):
    base_dir = tmp_path
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()

    started_at = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    install_date = started_at - timedelta(hours=1)
    payload = {"started_at": started_at.isoformat()}
    lock_path = lock_dir / lcd_screen.SUITE_UPTIME_LOCK_NAME
    lock_path.write_text(json.dumps(payload), encoding="utf-8")
    install_lock = lock_dir / lcd_screen.INSTALL_DATE_LOCK_NAME
    install_lock.write_text(install_date.isoformat(), encoding="utf-8")

    now = started_at + timedelta(hours=1, minutes=2)
    os.utime(lock_path, (now.timestamp(), now.timestamp()))
    uptime_payload = lcd_screen.LockPayload(
        "UP 0d0h0m AP",
        "ON 0m0s eth0",
        lcd_screen.DEFAULT_SCROLL_MS,
    )

    refreshed = lcd_screen._refresh_uptime_payload(
        uptime_payload, base_dir=base_dir, now=now
    )

    assert refreshed.line1 == "UP 0d1h2m AP"
    assert refreshed.line2 == "ON 0m0s eth0"


def test_refresh_uptime_payload_passes_through_non_uptime_payload(tmp_path):
    payload = lcd_screen.LockPayload("hello", "world", lcd_screen.DEFAULT_SCROLL_MS)

    refreshed = lcd_screen._refresh_uptime_payload(payload, base_dir=tmp_path)

    assert refreshed == payload


def test_uptime_seconds_ignores_stale_lock(monkeypatch, tmp_path):
    base_dir = tmp_path
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()

    now = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
    started_at = now - timedelta(days=1)
    lock_path = lock_dir / lcd_screen.SUITE_UPTIME_LOCK_NAME
    lock_path.write_text(json.dumps({"started_at": started_at.isoformat()}), encoding="utf-8")

    stale_timestamp = (now - timedelta(hours=1)).timestamp()
    os.utime(lock_path, (stale_timestamp, stale_timestamp))

    boot_time = now - timedelta(minutes=5)
    monkeypatch.setattr(
        lcd_screen.psutil, "boot_time", lambda: boot_time.timestamp()
    )

    assert lcd_screen._uptime_seconds(base_dir, now=now) == 300


def test_install_date_lock_created_when_missing(tmp_path):
    base_dir = tmp_path
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()

    now = datetime(2024, 3, 1, 8, 30, tzinfo=timezone.utc)
    install_date = lcd_screen._install_date(base_dir, now=now)

    assert install_date == now
    install_lock = lock_dir / lcd_screen.INSTALL_DATE_LOCK_NAME
    assert install_lock.exists()
    assert install_lock.read_text(encoding="utf-8").strip() == now.isoformat()


def test_select_low_payload_includes_ap_client_count(tmp_path, monkeypatch):
    base_dir = tmp_path
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()

    started_at = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    now = started_at + timedelta(minutes=5)
    lock_path = lock_dir / lcd_screen.SUITE_UPTIME_LOCK_NAME
    lock_path.write_text(json.dumps({"started_at": started_at.isoformat()}), encoding="utf-8")
    os.utime(lock_path, (now.timestamp(), now.timestamp()))
    install_lock = lock_dir / lcd_screen.INSTALL_DATE_LOCK_NAME
    install_lock.write_text((started_at - timedelta(hours=1)).isoformat(), encoding="utf-8")

    monkeypatch.setattr(lcd_screen, "_ap_mode_enabled", lambda: True)
    monkeypatch.setattr(lcd_screen, "_ap_client_count", lambda: 3)

    payload = lcd_screen.LockPayload("", "", lcd_screen.DEFAULT_SCROLL_MS)
    selected = lcd_screen._select_low_payload(payload, base_dir=base_dir, now=now)

    assert selected.line1 == "UP 0d0h5m AP3"


def test_format_on_label_keeps_large_minute_values():
    assert lcd_screen._format_on_label(100 * 60) == "100m0s"
