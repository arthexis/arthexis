from __future__ import annotations

import json

from django.db.utils import OperationalError, ProgrammingError

from apps.celery.utils import normalize_periodic_task_name

REPORT_TASK_NAME_PREFIX = "scheduled-sql-report"
REPORT_TASK_PATH = "apps.reports.tasks.run_scheduled_sql_reports"


def report_task_name(report_id: int) -> str:
    """Return the periodic task name for a scheduled SQL report."""

    return f"{REPORT_TASK_NAME_PREFIX}-{report_id}"


def sync_report_schedule(report) -> None:
    """Ensure beat schedules mirror the report scheduling fields."""

    try:
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
    except Exception:  # pragma: no cover - optional dependency
        return

    try:
        if not report.schedule_enabled:
            _clear_report_task(report, PeriodicTask)
            return

        cadence_interval = report.schedule_interval
        cadence_crontab = report.schedule_crontab
        if (
            cadence_interval is None
            and cadence_crontab is None
            and report.schedule_interval_minutes > 0
        ):
            cadence_interval, _ = IntervalSchedule.objects.get_or_create(
                every=report.schedule_interval_minutes,
                period=IntervalSchedule.MINUTES,
            )
            type(report).objects.filter(pk=report.pk).update(
                schedule_interval=cadence_interval
            )
            report.schedule_interval = cadence_interval

        if cadence_interval is None and cadence_crontab is None:
            _clear_report_task(report, PeriodicTask)
            return

        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, report_task_name(report.pk)
        )
        task, _ = PeriodicTask.objects.update_or_create(
            name=task_name,
            defaults={
                "crontab": cadence_crontab,
                "enabled": True,
                "interval": cadence_interval,
                "kwargs": json.dumps({"report_id": report.pk}, sort_keys=True),
                "start_time": report.next_scheduled_run_at,
                "task": REPORT_TASK_PATH,
            },
        )
        if report.schedule_periodic_task_id != task.pk:
            type(report).objects.filter(pk=report.pk).update(
                schedule_periodic_task=task
            )
            report.schedule_periodic_task_id = task.pk
    except (OperationalError, ProgrammingError):
        return


def _clear_report_task(report, PeriodicTask) -> None:
    """Delete any managed periodic task and unlink it from the report."""

    task = report.schedule_periodic_task
    if task is None:
        return
    task.delete()
    type(report).objects.filter(pk=report.pk).update(schedule_periodic_task=None)
    report.schedule_periodic_task_id = None


__all__ = ["report_task_name", "sync_report_schedule"]
