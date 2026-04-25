from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.core.tasks import log_retention


def _write_file(path: Path, *, days_old: int, content: str = "log\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_old)).timestamp()
    path.chmod(0o644)
    path.touch()
    os.utime(path, (stamp, stamp))


def test_run_log_retention_applies_two_year_default_to_archived_logs(settings, tmp_path):
    settings.LOG_DIR = str(tmp_path)
    _write_file(tmp_path / "archive" / "station.log.1", days_old=731)
    _write_file(tmp_path / "archive" / "station.log.1.recent", days_old=10)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 1
    assert not (tmp_path / "archive" / "station.log.1").exists()
    assert (tmp_path / "archive" / "station.log.1.recent").exists()


def test_run_log_retention_preserves_active_transactional_logs(settings, tmp_path):
    settings.LOG_DIR = str(tmp_path)
    _write_file(tmp_path / "error.log", days_old=365)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 0
    assert (tmp_path / "error.log").exists()


def test_run_log_retention_preserves_managed_active_artifacts(
    settings,
    tmp_path,
    monkeypatch,
):
    settings.LOG_DIR = str(tmp_path)
    monkeypatch.setattr(
        log_retention,
        "MANAGED_LOG_BASENAMES",
        {*log_retention.MANAGED_LOG_BASENAMES, "rfid-scans.ndjson"},
    )
    _write_file(tmp_path / "rfid-scans.ndjson", days_old=365)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 0
    assert (tmp_path / "rfid-scans.ndjson").exists()


def test_run_log_retention_preserves_dynamic_root_active_app_logs(settings, tmp_path):
    settings.LOG_DIR = str(tmp_path)
    _write_file(tmp_path / "front-desk.log", days_old=365)
    _write_file(tmp_path / "back-office.log", days_old=365)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 0
    assert (tmp_path / "front-desk.log").exists()
    assert (tmp_path / "back-office.log").exists()


def test_run_log_retention_preserves_non_log_files(settings, tmp_path):
    settings.LOG_DIR = str(tmp_path)
    _write_file(tmp_path / "content-drops" / "sample.json", days_old=900)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 0
    assert (tmp_path / "content-drops" / "sample.json").exists()


def test_run_log_retention_trims_stale_unmanaged_rotated_logs(settings, tmp_path):
    settings.LOG_DIR = str(tmp_path)
    _write_file(tmp_path / "command.log.1", days_old=731)
    _write_file(tmp_path / "error.log", days_old=365)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 1
    assert not (tmp_path / "command.log.1").exists()
    assert (tmp_path / "error.log").exists()


def test_run_log_retention_trims_stale_scan_and_session_logs(settings, tmp_path):
    settings.LOG_DIR = str(tmp_path)
    _write_file(tmp_path / "rfid-scans.ndjson", days_old=731)
    _write_file(tmp_path / "rfid-scans.rotated.ndjson", days_old=731)
    _write_file(
        tmp_path / "sessions" / "CID" / "202404240001.json",
        days_old=731,
        content="[]\n",
    )
    _write_file(tmp_path / "content-drops" / "sample.json", days_old=900)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 3
    assert not (tmp_path / "rfid-scans.ndjson").exists()
    assert not (tmp_path / "rfid-scans.rotated.ndjson").exists()
    assert not (tmp_path / "sessions" / "CID" / "202404240001.json").exists()
    assert (tmp_path / "content-drops" / "sample.json").exists()


def test_run_log_retention_scopes_session_json_to_log_dir_sessions_subtree(
    settings,
    tmp_path,
):
    log_dir = tmp_path / "sessions" / "logs"
    settings.LOG_DIR = str(log_dir)
    _write_file(
        log_dir / "sessions" / "CID" / "202404240001.json",
        days_old=731,
        content="[]\n",
    )
    _write_file(log_dir / "content-drops" / "sample.json", days_old=900)

    result = log_retention._run_log_retention()

    assert result.deleted_files == 1
    assert not (log_dir / "sessions" / "CID" / "202404240001.json").exists()
    assert (log_dir / "content-drops" / "sample.json").exists()


def test_run_log_retention_preserves_in_progress_session_json_during_disk_pressure(
    settings,
    tmp_path,
    monkeypatch,
):
    settings.LOG_DIR = str(tmp_path)
    _write_file(
        tmp_path / "sessions" / "CID" / "202404240001.json",
        days_old=31,
        content='[\n  {"message": "boot"}',
    )
    levels = iter([85.0, 85.0, 70.0])
    monkeypatch.setattr(log_retention, "_disk_usage_percent", lambda _path: next(levels))

    result = log_retention._run_log_retention()

    assert result.deleted_files == 0
    assert result.disk_percent == 70.0
    assert (tmp_path / "sessions" / "CID" / "202404240001.json").exists()


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
