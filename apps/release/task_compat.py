"""Temporary Celery task-name aliases for the core-to-release ownership move."""

from __future__ import annotations

from celery import shared_task

from apps.release.tasks.auto_upgrade.tasks import (
    check_github_updates as _check_github_updates,
)
from apps.release.tasks.auto_upgrade.tasks import (
    verify_auto_upgrade_health as _verify_auto_upgrade_health,
)
from apps.release.tasks.maintenance import (
    run_release_data_transform as _run_release_data_transform,
)
from apps.release.tasks.maintenance import (
    run_scheduled_release as _run_scheduled_release,
)


@shared_task(name="apps.core.tasks.auto_upgrade.tasks.check_github_updates")
def legacy_check_github_updates(*args, **kwargs):
    """Delegate historical queued task messages to the release-owned task."""

    return _check_github_updates.run(*args, **kwargs)


@shared_task(name="apps.core.tasks.auto_upgrade.tasks.verify_auto_upgrade_health")
def legacy_verify_auto_upgrade_health(*args, **kwargs):
    """Delegate historical queued health checks to the release-owned task."""

    return _verify_auto_upgrade_health.run(*args, **kwargs)


def _run_release_transform(*args, **kwargs):
    return _run_release_data_transform.run(*args, **kwargs)


def _run_scheduled(*args, **kwargs):
    return _run_scheduled_release.run(*args, **kwargs)


@shared_task(name="apps.core.tasks.maintenance._run_release_data_transform")
def legacy_private_release_data_transform(*args, **kwargs):
    return _run_release_transform(*args, **kwargs)


@shared_task(name="apps.core.tasks.maintenance.run_release_data_transform")
def legacy_maintenance_release_data_transform(*args, **kwargs):
    return _run_release_transform(*args, **kwargs)


@shared_task(name="apps.core.tasks.run_release_data_transform")
def legacy_release_data_transform(*args, **kwargs):
    return _run_release_transform(*args, **kwargs)


@shared_task(name="apps.core.tasks.maintenance._run_scheduled_release")
def legacy_private_scheduled_release(*args, **kwargs):
    return _run_scheduled(*args, **kwargs)


@shared_task(name="apps.core.tasks.maintenance.run_scheduled_release")
def legacy_maintenance_scheduled_release(*args, **kwargs):
    return _run_scheduled(*args, **kwargs)


@shared_task(name="apps.core.tasks.run_scheduled_release")
def legacy_scheduled_release(*args, **kwargs):
    return _run_scheduled(*args, **kwargs)
