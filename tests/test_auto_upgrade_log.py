from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from apps.core import system, tasks
from apps.core.tasks import _project_base_dir
from django.utils import timezone


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


@pytest.mark.django_db
def test_auto_upgrade_check_status_message(monkeypatch):
    captured: dict[str, str] = {}

    def fake_broadcast(*, subject: str, body: str, **kwargs):
        captured["subject"] = subject
        captured["body"] = body

    monkeypatch.setattr("apps.nodes.models.NetMessage.broadcast", fake_broadcast)

    fixed_now = timezone.make_aware(datetime(2024, 1, 1, 9, 5))
    monkeypatch.setattr(tasks.timezone, "now", lambda: fixed_now)

    tasks._broadcast_auto_upgrade_check_status("THIS-STATUS-IS-TOO-LONG")

    assert captured["subject"] == "UP-CHECK 09:05"
    assert captured["body"] == "THIS-STATUS-IS-T"


@pytest.mark.django_db
def test_check_github_updates_sends_status(monkeypatch, settings, tmp_path):
    statuses: list[str] = []

    monkeypatch.setattr(
        tasks, "_broadcast_auto_upgrade_check_status", lambda status: statuses.append(status)
    )
    monkeypatch.setattr(tasks, "_project_base_dir", lambda: tmp_path)
    settings.BASE_DIR = tmp_path

    mode = tasks.AutoUpgradeMode(
        mode="unstable",
        admin_override=False,
        override_log=None,
        mode_file_exists=True,
        mode_file_physical=True,
        interval_minutes=60,
    )

    monkeypatch.setattr(
        tasks, "_resolve_auto_upgrade_mode", lambda base_dir, channel_override: mode
    )
    monkeypatch.setattr(
        tasks, "_apply_stable_schedule_guard", lambda base_dir, mode, ops: True
    )

    log_file = tmp_path / "logs" / "auto-upgrade.log"

    def fake_log_auto_upgrade_trigger(base_dir):
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.touch()
        return log_file

    monkeypatch.setattr(tasks, "_log_auto_upgrade_trigger", fake_log_auto_upgrade_trigger)

    repo_state = tasks.AutoUpgradeRepositoryState(
        remote_revision="remote",
        release_version=None,
        release_revision=None,
        remote_version="1.0.0",
        local_version="1.0.0",
        local_revision="abc123",
        severity=tasks.SEVERITY_LOW,
    )

    monkeypatch.setattr(
        tasks,
        "_fetch_repository_state",
        lambda base_dir, branch, mode, ops, state: repo_state,
    )
    monkeypatch.setattr(
        tasks,
        "_plan_auto_upgrade",
        lambda base_dir, mode, repo_state, notify, startup, ops: None,
    )

    tasks.check_github_updates()

    assert statuses == ["NO-UPDATE"]
