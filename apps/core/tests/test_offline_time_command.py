from datetime import datetime, timedelta, timezone
import io

from django.core.management import call_command

from apps.core.management.commands import offline_time as offline_time_command


def test_offline_time_command_summarizes_segments(monkeypatch):
    now = datetime(2024, 1, 4, 12, 0, 0, tzinfo=timezone.utc)
    downtime_periods = [
        (now - timedelta(hours=60), now - timedelta(hours=58)),
        (now - timedelta(hours=30), now - timedelta(hours=26, minutes=30)),
    ]

    monkeypatch.setattr(offline_time_command.timezone, "now", lambda: now)
    monkeypatch.setattr(
        offline_time_command,
        "_load_shutdown_periods",
        lambda: (downtime_periods, None),
    )
    monkeypatch.setattr(offline_time_command, "_suite_offline_period", lambda *_: None)

    stdout = io.StringIO()
    call_command("offline_time", stdout=stdout)
    output = stdout.getvalue()

    assert "Suite offline/online summary (last 72 hours):" in output
    assert "Online: 66h30m0s" in output
    assert "Offline: 5h30m0s" in output
    assert "Offline:" in output
