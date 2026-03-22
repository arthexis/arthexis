from __future__ import annotations

from datetime import datetime, timezone

from apps.core import system_ui


def test_public_format_timestamp_handles_timezone_aware_datetime() -> None:
    """Public formatter returns a non-empty display string for aware datetimes."""

    value = datetime(2024, 1, 1, 12, 30, tzinfo=timezone.utc)
    assert system_ui.format_timestamp(value)


def test_public_read_startup_report_missing_file(tmp_path) -> None:
    """Public startup report helper exposes the missing-file sentinel payload."""

    report = system_ui.read_startup_report(base_dir=tmp_path)
    assert report["missing"] is True
    assert report["entries"] == []
