from __future__ import annotations

from datetime import datetime

import pytest
from django.utils import timezone

from apps.core import tasks


@pytest.mark.django_db
def test_send_auto_upgrade_check_message(monkeypatch):
    sent = []
    fixed_now = timezone.make_aware(datetime(2024, 1, 1, 12, 34))
    monkeypatch.setattr(tasks.timezone, "now", lambda: fixed_now)

    def fake_broadcast(cls, subject, body, reach=None, seen=None, attachments=None):
        sent.append({"subject": subject, "body": body})

    from apps.nodes.models.node_core import NetMessage

    monkeypatch.setattr(NetMessage, "broadcast", classmethod(fake_broadcast))

    tasks._send_auto_upgrade_check_message("APPLIED-SUCCESSFULLY")

    assert sent[0]["subject"] == "UP-CHECK 12:34"
    assert sent[0]["body"] == "APPLIED-SUCCESSF"


@pytest.mark.django_db
def test_check_github_updates_reports_status(monkeypatch, settings, tmp_path):
    statuses: list[str] = []
    settings.BASE_DIR = tmp_path
    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(tmp_path))

    monkeypatch.setattr(tasks, "_send_auto_upgrade_check_message", statuses.append)
    monkeypatch.setattr(
        tasks,
        "_resolve_auto_upgrade_mode",
        lambda base_dir, override: tasks.AutoUpgradeMode(
            mode="unstable",
            admin_override=False,
            override_log=None,
            mode_file_exists=True,
            mode_file_physical=True,
            interval_minutes=0,
        ),
    )
    monkeypatch.setattr(
        tasks, "_apply_stable_schedule_guard", lambda base_dir, mode, ops: True
    )
    repo_state = tasks.AutoUpgradeRepositoryState(
        remote_revision="newrev",
        release_version=None,
        release_revision=None,
        remote_version="1.0.1",
        local_version="1.0.0",
        local_revision="oldrev",
        severity=tasks.SEVERITY_NORMAL,
    )
    monkeypatch.setattr(
        tasks,
        "_fetch_repository_state",
        lambda base_dir, branch, mode, ops, state: repo_state,
    )
    monkeypatch.setattr(
        tasks,
        "_plan_auto_upgrade",
        lambda base_dir, mode, repo_state, notify, startup, ops: (["upgrade"], True),
    )
    monkeypatch.setattr(
        tasks, "_execute_upgrade_plan", lambda *args, **kwargs: None
    )

    ops = tasks.AutoUpgradeOperations(
        git_fetch=lambda *args, **kwargs: None,
        resolve_remote_revision=lambda *args, **kwargs: repo_state.remote_revision,
        ensure_runtime_services=lambda *args, **kwargs: None,
        delegate_upgrade=lambda *args, **kwargs: None,
        run_upgrade_command=lambda *args, **kwargs: (None, True),
    )

    tasks.check_github_updates(operations=ops)

    assert statuses == ["APPLIED"]

    monkeypatch.setattr(
        tasks,
        "_fetch_repository_state",
        lambda base_dir, branch, mode, ops, state: (_ for _ in ()).throw(
            RuntimeError("fetch failed")
        ),
    )

    with pytest.raises(RuntimeError):
        tasks.check_github_updates(operations=ops)

    assert statuses[-1] == "FAILED"
