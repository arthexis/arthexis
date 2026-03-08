"""Regression tests for Celery worker suite feature runtime controls."""

from __future__ import annotations


import pytest

from apps.features.models import Feature
from apps.services import celery_workers


@pytest.mark.django_db
def test_sync_celery_workers_from_feature_persists_lock_and_restarts(monkeypatch, tmp_path):
    """Regression: syncing celery workers writes lock file and restarts celery service."""

    Feature.objects.update_or_create(
        slug="celery-workers",
        defaults={
            "display": "Celery Workers",
            "source": Feature.Source.CUSTOM,
            "metadata": {"parameters": {"worker_count": "4"}},
        },
    )
    lock_path = tmp_path / ".locks" / "service.lck"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("demo\n", encoding="utf-8")

    monkeypatch.setattr("apps.services.celery_workers._systemctl_command", lambda: ["systemctl"])

    calls: list[list[str]] = []

    def _fake_run(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("apps.services.celery_workers.subprocess.run", _fake_run)

    worker_count, restarted = celery_workers.sync_celery_workers_from_feature(base_dir=tmp_path)

    assert worker_count == 4
    assert restarted is True
    assert (tmp_path / ".locks" / "celery_workers.lck").read_text(encoding="utf-8") == "4\n"
    assert calls == [["systemctl", "restart", "celery-demo.service"]]


@pytest.mark.django_db
def test_sync_celery_workers_without_systemctl_still_writes_lock(tmp_path):
    """Regression: lock file persists even when service restart is unavailable."""

    Feature.objects.update_or_create(
        slug="celery-workers",
        defaults={
            "display": "Celery Workers",
            "source": Feature.Source.CUSTOM,
            "metadata": {"parameters": {"worker_count": "3"}},
        },
    )

    worker_count, restarted = celery_workers.sync_celery_workers_from_feature(base_dir=tmp_path)

    assert worker_count == 3
    assert restarted is False
    assert (tmp_path / ".locks" / "celery_workers.lck").read_text(encoding="utf-8") == "3\n"
