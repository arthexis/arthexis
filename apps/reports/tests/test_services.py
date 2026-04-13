import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.reports.models import SQLReport, SQLReportProduct
from apps.reports.report_definitions import report_catalog
from apps.reports.services import (
    _render_pdf_bytes,
    run_due_scheduled_reports,
    run_sql_report,
)
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
    """Enabled schedules should default the next run timestamp when omitted."""

    before = timezone.now()
    report = SQLReport.objects.create(
        name="Scheduled report",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": "all"},
        schedule_enabled=True,
        schedule_interval_minutes=15,
    )
    after = timezone.now()

    assert report.next_scheduled_run_at is not None
    assert before <= report.next_scheduled_run_at <= after

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


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("report_type", "parameters"),
    [
        (
            SQLReport.ReportType.REPORT_PRODUCT_ACTIVITY,
            {"report_name_contains": "", "created_since": "", "limit": 10},
        ),
        (
            SQLReport.ReportType.SCHEDULED_REPORTS,
            {"schedule_state": "all", "name_contains": ""},
        ),
        (
            SQLReport.ReportType.SIGIL_ROOTS,
            {"context_type": "all"},
        ),
    ],
)
def test_catalog_templates_render_with_html_pdf_engine(monkeypatch, report_type, parameters):
    """Catalog report templates should render as HTML tables before PDF conversion."""

    captured_html: list[str] = []

    class FakeHTML:
        def __init__(self, string: str, url_fetcher):
            captured_html.append(string)
            with pytest.raises(ValueError, match="External resource loading is disabled"):
                url_fetcher("https://example.com/image.png")

        def write_pdf(self) -> bytes:
            return b"%PDF-fake"

    monkeypatch.setattr("apps.reports.services.HTML", FakeHTML)

    report = SQLReport.objects.create(
        name=f"{report_type} report",
        report_type=report_type,
        parameters=parameters,
    )

    result, product = run_sql_report(report)

    assert result.error is None
    assert product is not None
    assert product.pdf_content == b"%PDF-fake"
    assert captured_html
    assert "<table>" in captured_html[0]
    assert "<th>" in captured_html[0]


@pytest.mark.django_db
def test_report_pdf_rendering_gracefully_degrades_when_engine_missing(monkeypatch):
    """Missing HTML-to-PDF dependency should not fail report product generation."""

    monkeypatch.setattr("apps.reports.services.HTML", None)
    report = SQLReport.objects.create(
        name="No engine",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": "all"},
    )

    result, product = run_sql_report(report)

    assert result.error is None
    assert product is not None
    assert product.pdf_content == b""


@pytest.mark.django_db
def test_report_pdf_rendering_can_be_feature_flag_disabled(settings, monkeypatch):
    """Feature flag should allow runtime opt-out of PDF rendering dependencies."""

    class RaisingHTML:
        def __init__(self, string: str):
            raise AssertionError("HTML renderer should not be instantiated when disabled")

    settings.REPORTS_HTML_TO_PDF_ENABLED = False
    monkeypatch.setattr("apps.reports.services.HTML", RaisingHTML)

    report = SQLReport.objects.create(
        name="Disabled engine",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"context_type": "all"},
    )

    result, product = run_sql_report(report)

    assert result.error is None
    assert product is not None
    assert product.pdf_content == b""


def test_render_pdf_bytes_returns_empty_when_renderer_errors(monkeypatch):
    """Renderer errors should degrade to empty PDF bytes instead of bubbling up."""

    class FailingHTML:
        def __init__(self, string: str, url_fetcher):
            pass

        def write_pdf(self) -> bytes:
            raise OSError("missing cairo libs")

    monkeypatch.setattr("apps.reports.services.HTML", FailingHTML)

    assert _render_pdf_bytes("<table><tr><td>x</td></tr></table>") == b""
