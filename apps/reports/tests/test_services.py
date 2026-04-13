import pytest
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

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
def test_schedule_enabled_reports_default_next_run_at_on_save():
    """Enabled schedules should create beat tasks from legacy interval minutes."""

    report = SQLReport.objects.create(
        name="Scheduled report",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": "all"},
        schedule_enabled=True,
        schedule_interval_minutes=15,
    )

    report.refresh_from_db()
    assert report.schedule_interval is not None
    assert report.schedule_periodic_task is not None
    assert report.schedule_periodic_task.task == "apps.reports.tasks.run_scheduled_sql_reports"

@pytest.mark.django_db
def test_run_due_scheduled_reports_runs_requested_ids_only():
    """Only requested scheduled reports should execute."""

    now = timezone.now()
    due = SQLReport.objects.create(
        name="Due report",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": "all"},
        schedule_enabled=True,
        schedule_interval_minutes=30,
    )
    not_requested = SQLReport.objects.create(
        name="Not due report",
        report_type=SQLReport.ReportType.SCHEDULED_REPORTS,
        parameters={"schedule_state": "all", "name_contains": ""},
        schedule_enabled=True,
        schedule_interval_minutes=30,
    )

    processed = run_due_scheduled_reports(report_ids=[due.pk], now=now)

    assert processed == 1
    assert SQLReportProduct.objects.filter(report=due).exists()
    assert not SQLReportProduct.objects.filter(report=not_requested).exists()


@pytest.mark.django_db
def test_schedule_disabled_reports_remove_periodic_task():
    """Disabling schedules should remove managed beat tasks."""

    report = SQLReport.objects.create(
        name="Toggle schedule report",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": "all"},
        schedule_enabled=True,
        schedule_interval_minutes=5,
    )
    task_id = report.schedule_periodic_task_id
    assert task_id is not None

    report.schedule_enabled = False
    report.save()
    report.refresh_from_db()

    assert report.schedule_periodic_task is None
    assert not PeriodicTask.objects.filter(pk=task_id).exists()

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
