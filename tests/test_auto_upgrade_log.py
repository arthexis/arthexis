from __future__ import annotations

from pathlib import Path

import pytest

from apps.core import system
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
