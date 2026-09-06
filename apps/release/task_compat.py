"""Temporary Celery task-name aliases for the core-to-release ownership move."""

from __future__ import annotations

from celery import shared_task

from apps.release.tasks.auto_upgrade.tasks import (
    check_github_updates as _check_github_updates,
    verify_auto_upgrade_health as _verify_auto_upgrade_health,
)


@shared_task(name="apps.core.tasks.auto_upgrade.tasks.check_github_updates")
def legacy_check_github_updates(*args, **kwargs):
    """Delegate historical queued task messages to the release-owned task."""

    return _check_github_updates.run(*args, **kwargs)


@shared_task(name="apps.core.tasks.auto_upgrade.tasks.verify_auto_upgrade_health")
def legacy_verify_auto_upgrade_health(*args, **kwargs):
    """Delegate historical queued health checks to the release-owned task."""

    return _verify_auto_upgrade_health.run(*args, **kwargs)
