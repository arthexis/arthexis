import json
from datetime import datetime, timedelta, timezone

from apps.screens import lcd_screen


def test_refresh_uptime_payload_updates_subject(tmp_path):
    base_dir = tmp_path
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir()

    started_at = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    payload = {"started_at": started_at.isoformat()}
    (lock_dir / lcd_screen.SUITE_UPTIME_LOCK_NAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )

    now = started_at + timedelta(hours=1, minutes=2)
    uptime_payload = lcd_screen.LockPayload(
        "UP 0d0h0m ROLE", "ON 0h0m iface", lcd_screen.DEFAULT_SCROLL_MS
    )

    refreshed = lcd_screen._refresh_uptime_payload(
        uptime_payload, base_dir=base_dir, now=now
    )

    assert refreshed.line1 == "UP 0d1h2m ROLE"
    assert refreshed.line2 == uptime_payload.line2


def test_refresh_uptime_payload_passes_through_non_uptime_payload(tmp_path):
    payload = lcd_screen.LockPayload("hello", "world", lcd_screen.DEFAULT_SCROLL_MS)

    refreshed = lcd_screen._refresh_uptime_payload(payload, base_dir=tmp_path)

    assert refreshed == payload
