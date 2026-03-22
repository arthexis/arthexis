from datetime import timedelta

import pytest
from django.utils import timezone

from apps.reports.models import SQLReport, SQLReportProduct
from apps.reports.report_definitions import report_catalog
from apps.reports.services import run_due_scheduled_reports, run_sql_report
from apps.sigils.models import SigilRoot


@pytest.mark.django_db
def test_run_named_report_generates_html_and_pdf_products():
    """Running a named report should persist a rendered product artifact."""

    report = SQLReport.objects.create(
        name="Sigil catalog",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": "all"},
    )

    result, product = run_sql_report(report)

    assert result.error is None
    assert product is not None
    assert SQLReportProduct.objects.filter(report=report).count() == 1
    assert "Sigil catalog" in product.html_content
    assert product.pdf_content
    assert product.renderer_template_name == "reports/sql/sigil_roots.html"

    report.refresh_from_db()
    assert report.last_run_at is not None
    assert report.last_run_duration is not None


@pytest.mark.django_db
def test_run_due_scheduled_reports_runs_due_only():
    """Only due scheduled reports should execute and advance next run timestamp."""

    now = timezone.now()
    due = SQLReport.objects.create(
        name="Due report",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": "all"},
        schedule_enabled=True,
        schedule_interval_minutes=30,
        next_scheduled_run_at=now - timedelta(minutes=1),
    )
    SQLReport.objects.create(
        name="Not due report",
        report_type=SQLReport.ReportType.SCHEDULED_REPORTS,
        parameters={"schedule_state": "all", "name_contains": ""},
        schedule_enabled=True,
        schedule_interval_minutes=30,
        next_scheduled_run_at=now + timedelta(minutes=30),
    )

    processed = run_due_scheduled_reports(now=now)

    assert processed == 1
    assert SQLReportProduct.objects.filter(report=due).exists()

    due.refresh_from_db()
    assert due.next_scheduled_run_at == now + timedelta(minutes=30)


@pytest.mark.django_db
def test_run_sql_report_validation_failure_returns_error():
    """Parameter validation failures should be captured as execution errors."""

    report = SQLReport.objects.create(
        name="Broken scheduled report",
        report_type=SQLReport.ReportType.SCHEDULED_REPORTS,
        parameters={"schedule_state": "all", "name_contains": ""},
        schedule_enabled=False,
    )
    SQLReport.objects.filter(pk=report.pk).update(parameters={"schedule_state": "invalid"})
    report.refresh_from_db()

    result, product = run_sql_report(report)

    assert product is None
    assert result.error is not None
    assert "valid schedule state filter" in result.error


@pytest.mark.django_db
def test_run_due_scheduled_reports_skips_archived_legacy_reports():
    """Scheduler should ignore archived legacy definitions even when due."""

    now = timezone.now()
    archived = SQLReport.objects.create(
        name="Legacy report",
        report_type=SQLReport.ReportType.LEGACY_ARCHIVED,
        parameters={},
        legacy_definition={"query": "SELECT 1", "database_alias": "default"},
        schedule_enabled=True,
        schedule_interval_minutes=30,
        next_scheduled_run_at=now - timedelta(minutes=1),
    )
    healthy = SQLReport.objects.create(
        name="Healthy report",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": "all"},
        schedule_enabled=True,
        schedule_interval_minutes=30,
        next_scheduled_run_at=now - timedelta(minutes=1),
    )

    processed = run_due_scheduled_reports(now=now)

    assert processed == 1
    assert not SQLReportProduct.objects.filter(report=archived).exists()
    assert SQLReportProduct.objects.filter(report=healthy).exists()


@pytest.mark.django_db
def test_catalog_reports_are_shipped_in_code():
    """The maintained report catalog should expose shipped implementations."""

    catalog = report_catalog()

    assert [entry["key"] for entry in catalog] == [
        SQLReport.ReportType.REPORT_PRODUCT_ACTIVITY,
        SQLReport.ReportType.SCHEDULED_REPORTS,
        SQLReport.ReportType.SIGIL_ROOTS,
    ]


@pytest.mark.django_db
def test_sigil_root_report_uses_orm_backed_results():
    """Sigil root report should query approved ORM-backed data only."""

    SigilRoot.objects.update_or_create(
        prefix="REPORT_NODE_TEST",
        defaults={"context_type": SigilRoot.Context.ENTITY},
    )
    report = SQLReport.objects.create(
        name="Sigil roots only",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": SigilRoot.Context.ENTITY},
    )

    result, product = run_sql_report(report)

    assert result.error is None
    assert product is not None
    assert result.row_count >= 1
    assert any(row[0] == "REPORT_NODE_TEST" for row in result.rows)
