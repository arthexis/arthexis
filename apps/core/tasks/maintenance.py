from __future__ import annotations

import logging

from celery import shared_task

from apps.release import release_workflow

from .auto_upgrade import _get_package_release_model


logger = logging.getLogger(__name__)


@shared_task
def poll_emails() -> None:
    """Poll all configured email collectors for new messages."""
    try:
        from apps.emails.models import EmailCollector
    except Exception:  # pragma: no cover - app not ready
        return

    for collector in EmailCollector.objects.all():
        collector.collect()


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


@shared_task
def run_scheduled_release(release_id: int) -> None:
    """Entrypoint used by django-celery-beat to trigger scheduled releases."""

    execute_scheduled_release(release_id)


@shared_task
def run_client_report_schedule(schedule_id: int) -> None:
    """Execute a :class:`core.models.ClientReportSchedule` run."""

    from apps.energy.models import ClientReportSchedule

    schedule = ClientReportSchedule.objects.filter(pk=schedule_id).first()
    if not schedule:
        logger.warning("ClientReportSchedule %s no longer exists", schedule_id)
        return

    try:
        schedule.run()
    except Exception:
        logger.exception("ClientReportSchedule %s failed", schedule_id)
        raise
