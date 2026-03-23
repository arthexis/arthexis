from celery import shared_task

from apps.reports.services import run_due_scheduled_reports


@shared_task(name="apps.reports.tasks.run_scheduled_sql_reports")
def run_scheduled_sql_reports() -> int:
    """Execute all due scheduled SQL reports and return processed count."""

    return run_due_scheduled_reports()
