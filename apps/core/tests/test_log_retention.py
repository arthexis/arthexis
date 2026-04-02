from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.core.tasks import log_retention


def _write_file(path: Path, *, days_old: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("log\n", encoding="utf-8")
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_old)).timestamp()
    path.chmod(0o644)
    path.touch()
    os.utime(path, (stamp, stamp))


def test_run_log_retention_applies_two_year_default(settings, tmp_path):
    settings.LOG_DIR = str(tmp_path)
    _write_file(tmp_path / "sessions" / "old-session.json", days_old=731)
    _write_file(tmp_path / "sessions" / "recent-session.json", days_old=10)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 1
    assert not (tmp_path / "sessions" / "old-session.json").exists()
    assert (tmp_path / "sessions" / "recent-session.json").exists()


def test_run_log_retention_preserves_lower_transactional_retention(settings, tmp_path):
    settings.LOG_DIR = str(tmp_path)
    _write_file(tmp_path / "error.log", days_old=4)
    _write_file(tmp_path / "sessions" / "session.json", days_old=4)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 1
    assert not (tmp_path / "error.log").exists()
    assert (tmp_path / "sessions" / "session.json").exists()


def test_run_log_retention_sends_alert_when_disk_remains_high(settings, tmp_path, monkeypatch):
    settings.LOG_DIR = str(tmp_path)

    monkeypatch.setattr(log_retention, "_trim_with_policy", lambda _log_dir: (0, 0))
    monkeypatch.setattr(log_retention, "_delete_candidates", lambda _log_dir, max_age_days: (1, 10))

    levels = iter([85.0, 85.0, 85.0, 85.0, 85.0, 85.0])
    monkeypatch.setattr(log_retention, "_disk_usage_percent", lambda _path: next(levels))

    calls: list[tuple[float, float]] = []

    def _record_alert(*, before_percent: float, after_percent: float) -> bool:
        calls.append((before_percent, after_percent))
        return True

    monkeypatch.setattr(log_retention, "_send_disk_pressure_alert", _record_alert)

    result = log_retention._run_log_retention()

    assert result.alert_sent is True
    assert calls == [(85.0, 85.0)]
