from datetime import timedelta

import pytest
from django.utils import timezone

from apps.reports.models import SQLReport, SQLReportProduct
from apps.reports.services import run_due_scheduled_reports, run_sql_report


@pytest.mark.django_db
def test_run_sql_report_generates_html_and_pdf_products():
    """Running a SQL report should persist a rendered product artifact."""

    report = SQLReport.objects.create(
        name="Smoke report",
        database_alias="default",
        query="SELECT 1 AS sample_value",
    )

    result, product = run_sql_report(report)

    assert result.error is None
    assert product is not None
    assert SQLReportProduct.objects.filter(report=report).count() == 1
    assert "Smoke report" in product.html_content
    assert product.pdf_content

    report.refresh_from_db()
    assert report.last_run_at is not None
    assert report.last_run_duration is not None


@pytest.mark.django_db
def test_run_due_scheduled_reports_runs_due_only():
    """Only due scheduled reports should execute and advance next run timestamp."""

    now = timezone.now()
    due = SQLReport.objects.create(
        name="Due report",
        database_alias="default",
        query="SELECT 1",
        schedule_enabled=True,
        schedule_interval_minutes=30,
        next_scheduled_run_at=now - timedelta(minutes=1),
    )
    SQLReport.objects.create(
        name="Not due report",
        database_alias="default",
        query="SELECT 2",
        schedule_enabled=True,
        schedule_interval_minutes=30,
        next_scheduled_run_at=now + timedelta(minutes=30),
    )

    processed = run_due_scheduled_reports(now=now)

    assert processed == 1
    assert SQLReportProduct.objects.filter(report=due).exists()

    due.refresh_from_db()
    assert due.next_scheduled_run_at == now + timedelta(minutes=30)
