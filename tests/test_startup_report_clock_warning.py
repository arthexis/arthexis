from datetime import datetime, timedelta

from django.utils import timezone

from core import system


def _write_startup_entry(log_path, timestamp_text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"{timestamp_text}\tstart.sh\tstart\tinvoked\n", encoding="utf-8"
    )


def test_startup_report_warns_when_clock_ahead(monkeypatch, tmp_path):
    now = timezone.make_aware(datetime(2024, 11, 20, 10, 0))
    monkeypatch.setattr(system.timezone, "now", lambda: now)

    log_path = tmp_path / "logs" / system.STARTUP_REPORT_LOG_NAME
    future_timestamp = (now + timedelta(days=1, hours=2)).isoformat()
    _write_startup_entry(log_path, future_timestamp)

    report = system._read_startup_report(base_dir=tmp_path)

    warning = report.get("clock_warning")
    assert warning
    assert "ahead" in warning.lower()


def test_startup_report_warns_when_clock_behind(monkeypatch, tmp_path):
    now = timezone.make_aware(datetime(2024, 11, 20, 10, 0))
    monkeypatch.setattr(system.timezone, "now", lambda: now)

    log_path = tmp_path / "logs" / system.STARTUP_REPORT_LOG_NAME
    past_timestamp = (now - timedelta(days=2)).isoformat()
    _write_startup_entry(log_path, past_timestamp)

    report = system._read_startup_report(base_dir=tmp_path)

    warning = report.get("clock_warning")
    assert warning
    assert "behind" in warning.lower()


def test_startup_report_ignores_small_clock_offset(monkeypatch, tmp_path):
    now = timezone.make_aware(datetime(2024, 11, 20, 10, 0))
    monkeypatch.setattr(system.timezone, "now", lambda: now)

    log_path = tmp_path / "logs" / system.STARTUP_REPORT_LOG_NAME
    slight_offset = (now + timedelta(minutes=3)).isoformat()
    _write_startup_entry(log_path, slight_offset)

    report = system._read_startup_report(base_dir=tmp_path)

    assert report.get("clock_warning") is None
