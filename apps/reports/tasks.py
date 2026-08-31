from celery import shared_task

from apps.reports.services import run_due_scheduled_reports


@shared_task(name="apps.reports.tasks.run_scheduled_sql_reports")
def run_scheduled_sql_reports(
    report_id: int | None = None, report_ids: list[int] | None = None
) -> int:
    """Execute scheduled SQL reports identified by task payload report IDs."""

    selected_ids = list(report_ids or [])
    if report_id:
        selected_ids.append(int(report_id))
    return run_due_scheduled_reports(report_ids=selected_ids)
