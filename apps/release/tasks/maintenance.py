from __future__ import annotations

import logging

from celery import shared_task

from apps.release import release_workflow

from .utils import _get_package_release_model


logger = logging.getLogger(__name__)


def execute_scheduled_release(release_id: int) -> None:
    """Run the automated release flow for a scheduled PackageRelease."""

    model = _get_package_release_model()
    if model is None:
        logger.warning("Scheduled release %s skipped: model unavailable", release_id)
        return

    release = model.objects.filter(pk=release_id).first()
    if release is None:
        logger.warning("Scheduled release %s skipped: release not found", release_id)
        return

    try:
        release_workflow.run_headless_publish(release, auto_release=True)
    finally:
        release.clear_schedule(save=True)


def _run_scheduled_release(release_id: int) -> None:
    """Entrypoint used by django-celery-beat to trigger scheduled releases."""

    execute_scheduled_release(release_id)


run_scheduled_release = shared_task(_run_scheduled_release)


def _run_release_data_transform(transform_name: str) -> None:
    """Execute one deferred release data transform."""

    from apps.release.domain import run_transform

    try:
        result = run_transform(transform_name)
    except KeyError:
        logger.warning("Unknown release transform %s", transform_name)
        return

    logger.info(
        "Release transform %s processed=%s updated=%s complete=%s",
        transform_name,
        result.processed,
        result.updated,
        result.complete,
    )


run_release_data_transform = shared_task(_run_release_data_transform)


__all__ = [
    "_run_release_data_transform",
    "_run_scheduled_release",
    "execute_scheduled_release",
    "run_release_data_transform",
    "run_scheduled_release",
]
