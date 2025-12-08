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
