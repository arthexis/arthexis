from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.core import tasks
from apps.nodes.models import Node


@pytest.mark.django_db
def test_canary_gate_blocks_when_canary_offline(monkeypatch, tmp_path: Path):
    now = timezone.now()
    local = Node.objects.create(hostname="local")
    canary = Node.objects.create(hostname="canary", installed_revision="rev-1")
    Node.objects.filter(pk=canary.pk).update(
        last_updated=now - timedelta(minutes=30)
    )
    local.upgrade_canaries.add(canary)

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local))
    monkeypatch.setattr(tasks, "append_auto_upgrade_log", lambda *args, **kwargs: None)

    repo_state = tasks.AutoUpgradeRepositoryState(
        remote_revision="rev-1",
        release_version=None,
        release_revision=None,
        remote_version="1.0.0",
        local_version="0.9.0",
        local_revision="rev-0",
        severity=tasks.SEVERITY_NORMAL,
    )
    mode = tasks.AutoUpgradeMode(
        mode="unstable",
        admin_override=False,
        override_log=None,
        mode_file_exists=True,
        mode_file_physical=True,
        interval_minutes=60,
    )

    assert tasks._canary_gate(tmp_path, repo_state, mode, now=now) is False


@pytest.mark.django_db
def test_canary_gate_allows_when_canary_ready(monkeypatch, tmp_path: Path):
    now = timezone.now()
    local = Node.objects.create(hostname="local")
    canary = Node.objects.create(hostname="canary", installed_revision="rev-2")
    local.upgrade_canaries.add(canary)

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local))
    monkeypatch.setattr(tasks, "append_auto_upgrade_log", lambda *args, **kwargs: None)

    repo_state = tasks.AutoUpgradeRepositoryState(
        remote_revision="rev-2",
        release_version=None,
        release_revision=None,
        remote_version="1.2.0",
        local_version="1.1.0",
        local_revision="rev-1",
        severity=tasks.SEVERITY_NORMAL,
    )
    mode = tasks.AutoUpgradeMode(
        mode="unstable",
        admin_override=False,
        override_log=None,
        mode_file_exists=True,
        mode_file_physical=True,
        interval_minutes=60,
    )

    assert tasks._canary_gate(tmp_path, repo_state, mode, now=now) is True


@pytest.mark.django_db
def test_canary_gate_blocks_when_canary_version_mismatch(monkeypatch, tmp_path: Path):
    now = timezone.now()
    local = Node.objects.create(hostname="local")
    canary = Node.objects.create(hostname="canary", installed_version="2.0.0")
    local.upgrade_canaries.add(canary)

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local))
    monkeypatch.setattr(tasks, "append_auto_upgrade_log", lambda *args, **kwargs: None)

    repo_state = tasks.AutoUpgradeRepositoryState(
        remote_revision="rev-3",
        release_version="2.1.0",
        release_revision=None,
        remote_version="2.1.0",
        local_version="2.0.0",
        local_revision="rev-2",
        severity=tasks.SEVERITY_NORMAL,
    )
    mode = tasks.AutoUpgradeMode(
        mode="stable",
        admin_override=False,
        override_log=None,
        mode_file_exists=True,
        mode_file_physical=True,
        interval_minutes=60,
    )

    assert tasks._canary_gate(tmp_path, repo_state, mode, now=now) is False
