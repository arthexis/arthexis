from __future__ import annotations

import logging

from celery import shared_task

from apps.release.tasks.maintenance import (
    _run_release_data_transform,
    _run_scheduled_release,
    execute_scheduled_release,
    run_release_data_transform,
    run_scheduled_release,
)


logger = logging.getLogger(__name__)


def _poll_emails() -> None:
    """Compatibility entry point for the email-owned polling task."""
    from apps.emails.tasks import _poll_emails as poll_emails_impl

    poll_emails_impl()


poll_emails = shared_task(_poll_emails)


def _run_client_report_schedule(schedule_id: int) -> None:
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


run_client_report_schedule = shared_task(_run_client_report_schedule)


__all__ = [
    "_poll_emails",
    "_run_client_report_schedule",
    "_run_release_data_transform",
    "_run_scheduled_release",
    "execute_scheduled_release",
    "poll_emails",
    "run_client_report_schedule",
    "run_release_data_transform",
    "run_scheduled_release",
]
