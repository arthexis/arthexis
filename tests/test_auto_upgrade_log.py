from __future__ import annotations

from pathlib import Path

import pytest

from apps.core import system, tasks
from apps.core.tasks import _project_base_dir


@pytest.mark.django_db
def test_project_base_dir_prefers_environment(monkeypatch, settings, tmp_path):
    env_base = tmp_path / "runtime"
    env_base.mkdir()

    settings.BASE_DIR = tmp_path / "settings"
    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(env_base))

    assert _project_base_dir() == env_base


@pytest.mark.django_db
def test_auto_upgrade_report_reads_from_env_base(monkeypatch, settings, tmp_path):
    env_base = tmp_path / "runtime"
    log_dir = env_base / "logs"
    log_dir.mkdir(parents=True)

    log_file = log_dir / "auto-upgrade.log"
    log_file.write_text("2024-01-01T00:00:00+00:00 logged entry\n", encoding="utf-8")

    settings.BASE_DIR = tmp_path / "settings"
    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(env_base))

    report = system._build_auto_upgrade_report()

    assert report["log_entries"][0]["message"] == "logged entry"
    assert Path(report["settings"]["log_path"]) == log_file


@pytest.mark.django_db
def test_auto_upgrade_report_uses_log_timestamp_when_schedule_missing(
    monkeypatch, settings, tmp_path
):
    env_base = tmp_path / "runtime"
    log_dir = env_base / "logs"
    log_dir.mkdir(parents=True)

    log_file = log_dir / "auto-upgrade.log"
    log_file.write_text("2024-01-01T00:00:00+00:00 logged entry\n", encoding="utf-8")

    settings.BASE_DIR = tmp_path / "settings"
    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(env_base))

    monkeypatch.setattr(
        system,
        "_load_auto_upgrade_schedule",
        lambda: {"available": True, "configured": True, "last_run_at": ""},
    )

    report = system._build_auto_upgrade_report()

    assert report["schedule"]["last_run_at"] == report["log_entries"][0]["timestamp"]


@pytest.mark.django_db
def test_auto_upgrade_summary_highlights_last_activity(monkeypatch, settings, tmp_path):
    env_base = tmp_path / "runtime"
    log_dir = env_base / "logs"
    log_dir.mkdir(parents=True)

    log_file = log_dir / "auto-upgrade.log"
    log_file.write_text("2024-01-01T00:00:00+00:00 logged entry\n", encoding="utf-8")

    settings.BASE_DIR = tmp_path / "settings"
    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(env_base))

    monkeypatch.setattr(
        system,
        "_load_auto_upgrade_schedule",
        lambda: {
            "available": True,
            "configured": True,
            "enabled": True,
            "next_run": "2024-01-02 00:00",
            "failure_count": 1,
        },
    )

    report = system._build_auto_upgrade_report()

    assert report["summary"]["last_activity"]["message"] == "logged entry"
    assert report["summary"]["next_run"] == "2024-01-02 00:00"
    assert any(
        "recorded upgrade failure" in issue["label"] for issue in report["summary"]["issues"]
    )


@pytest.mark.django_db
def test_auto_upgrade_report_marks_fast_lane(monkeypatch, settings, tmp_path):
    env_base = tmp_path / "runtime"
    lock_dir = env_base / ".locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "auto_upgrade_fast_lane.lck").touch()

    settings.BASE_DIR = tmp_path / "settings"
    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(env_base))

    report = system._build_auto_upgrade_report()

    assert report["settings"]["fast_lane_enabled"]
    assert report["schedule"]["fast_lane_enabled"]
    assert "hour" in report["schedule"]["description"].lower()


@pytest.mark.django_db
def test_auto_upgrade_report_fast_lane_next_run_uses_log_timestamp(
    monkeypatch, settings, tmp_path
):
    env_base = tmp_path / "runtime"
    lock_dir = env_base / ".locks"
    log_dir = env_base / "logs"
    lock_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    (lock_dir / "auto_upgrade_fast_lane.lck").touch()

    log_file = log_dir / "auto-upgrade.log"
    log_file.write_text("2024-01-01T12:44:00+00:00 logged entry\n", encoding="utf-8")

    settings.BASE_DIR = tmp_path / "settings"
    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(env_base))

    monkeypatch.setattr(
        system,
        "_load_auto_upgrade_schedule",
        lambda: {"available": True, "configured": True, "last_run_at": "", "next_run": ""},
    )

    report = system._build_auto_upgrade_report()

    assert report["schedule"]["last_run_at"] == report["log_entries"][0]["timestamp"]
    assert report["schedule"]["next_run"]


def test_trigger_upgrade_check_runs_inline_with_memory_broker(monkeypatch, settings):
    calls: list[str | None] = []

    class Runner:
        def __call__(self, channel_override=None):
            calls.append(channel_override)

        def delay(self, channel_override=None):  # pragma: no cover - defensive
            raise AssertionError("delay should not be used")

    monkeypatch.setattr(system, "check_github_updates", Runner())
    settings.CELERY_BROKER_URL = "memory://"

    queued = system._trigger_upgrade_check()

    assert not queued
    assert calls == [None]


def test_health_check_failure_without_revision(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks, "get_revision", lambda: "")

    tasks._handle_failed_health_check(tmp_path, detail="probe failed")

    log_file = tmp_path / "logs" / "auto-upgrade.log"
    log_entries = log_file.read_text(encoding="utf-8").splitlines()

    assert any(
        "Health check failed; manual intervention required" in line
        for line in log_entries
    )
    skip_lock = tmp_path / ".locks" / tasks.AUTO_UPGRADE_SKIP_LOCK_NAME
    assert not skip_lock.exists()


def test_health_check_failure_records_revision(monkeypatch, tmp_path):
    revision = "abc123"
    monkeypatch.setattr(tasks, "get_revision", lambda: revision)

    tasks._handle_failed_health_check(tmp_path, detail="probe failed")

    skip_lock = tmp_path / ".locks" / tasks.AUTO_UPGRADE_SKIP_LOCK_NAME
    assert skip_lock.read_text(encoding="utf-8").strip() == revision

    log_file = tmp_path / "logs" / "auto-upgrade.log"
    log_entries = log_file.read_text(encoding="utf-8").splitlines()
    assert any(f"Recorded blocked revision {revision}" in line for line in log_entries)
