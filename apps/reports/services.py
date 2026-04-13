from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from time import perf_counter
from typing import Any

from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from .models import SQLReport, SQLReportProduct
from .report_definitions import get_report_definition, report_catalog

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SQLExecutionResult:
    """Structured named-report output used by rendering flows.

    Parameters:
        columns: Ordered column labels.
        rows: Tabular results.
        row_count: Number of rows returned.
        report_type: Executed report type.
        executed_at: Timestamp for the execution.
        duration_ms: Execution duration in milliseconds.
        error: Human-readable error message.
        details: Additional execution metadata.

    Returns:
        ``SQLExecutionResult`` instance.
    """

    columns: list[str]
    rows: list[tuple[Any, ...]]
    row_count: int
    report_type: str
    executed_at: timezone.datetime
    duration_ms: float | None
    error: str | None
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a template/admin friendly representation.

        Parameters:
            None.

        Returns:
            Serialized execution payload.
        """

        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "report_type": self.report_type,
            "executed_at": self.executed_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "details": self.details,
        }


def render_report_product(
    sql_report: SQLReport, result: SQLExecutionResult
) -> SQLReportProduct:
    """Render and persist HTML/PDF outputs for a named report execution.

    Parameters:
        sql_report: Configured report record.
        result: Structured execution payload.

    Returns:
        Persisted ``SQLReportProduct`` instance.
    """

    definition = get_report_definition(sql_report.report_type)
    context = {
        "catalog": report_catalog(),
        "execution": result.as_dict(),
        "report": sql_report,
        "report_definition": definition,
    }
    html_content = render_to_string(definition.template_name, context)
    pdf_bytes = _render_pdf_bytes(html_content)

    return SQLReportProduct.objects.create(
        report=sql_report,
        report_type=sql_report.report_type,
        parameters=sql_report.parameters,
        renderer_template_name=definition.template_name,
        execution_details=result.details,
        row_count=result.row_count,
        duration_ms=result.duration_ms,
        html_content=html_content,
        pdf_content=pdf_bytes,
    )


def run_sql_report(
    sql_report: SQLReport,
) -> tuple[SQLExecutionResult, SQLReportProduct | None]:
    """Run a named report implementation and persist rendered products.

    Parameters:
        sql_report: Configured report record.

    Returns:
        A tuple of execution result and optional rendered product.
    """

    executed_at = timezone.now()
    started = perf_counter()

    try:
        definition = get_report_definition(sql_report.report_type)
        parameters = definition.clean_parameters(sql_report.parameters)
        execution = definition.execute(parameters)
        result = SQLExecutionResult(
            columns=execution.columns,
            rows=execution.rows,
            row_count=execution.row_count,
            report_type=sql_report.report_type,
            executed_at=execution.executed_at,
            duration_ms=execution.duration_ms,
            error=None,
            details=execution.details,
        )
    except ValidationError as exc:
        result = SQLExecutionResult(
            columns=[],
            rows=[],
            row_count=0,
            report_type=sql_report.report_type,
            executed_at=executed_at,
            duration_ms=(perf_counter() - started) * 1000,
            error="; ".join(exc.messages),
            details={},
        )
        return result, None
    except Exception as exc:  # pragma: no cover - defensive path
        logger.exception(
            "Unexpected error executing report", extra={"report_id": sql_report.pk}
        )
        result = SQLExecutionResult(
            columns=[],
            rows=[],
            row_count=0,
            report_type=sql_report.report_type,
            executed_at=executed_at,
            duration_ms=(perf_counter() - started) * 1000,
            error=str(exc),
            details={},
        )
        return result, None

    SQLReport.objects.filter(pk=sql_report.pk).update(
        last_run_at=result.executed_at,
        last_run_duration=timedelta(milliseconds=result.duration_ms or 0),
        updated_at=timezone.now(),
        parameters=parameters,
    )
    sql_report.refresh_from_db(
        fields=["last_run_at", "last_run_duration", "parameters"]
    )

    try:
        product = render_report_product(sql_report, result)
    except Exception as exc:  # pragma: no cover - defensive path
        logger.exception(
            "Unable to render report product", extra={"report_id": sql_report.pk}
        )
        result.error = str(exc)
        return result, None

    return result, product


def run_due_scheduled_reports(
    report_ids: list[int] | tuple[int, ...] | None = None,
    now: timezone.datetime | None = None,
) -> int:
    """Run explicitly targeted scheduled reports.

    Parameters:
        report_ids: Explicit report IDs to execute.
        now: Optional reference timestamp used for legacy fallback.

    Returns:
        Number of successfully processed reports.
    """

    selected_ids = [int(report_id) for report_id in (report_ids or []) if report_id]
    if not selected_ids:
        current = now or timezone.now()
        selected_ids = list(
            SQLReport.objects.filter(
                schedule_enabled=True,
                schedule_interval_minutes__gt=0,
                next_scheduled_run_at__lte=current,
                schedule_periodic_task__isnull=True,
            )
            .exclude(report_type=SQLReport.ReportType.LEGACY_ARCHIVED)
            .values_list("pk", flat=True)
        )

    due_reports = SQLReport.objects.filter(
        pk__in=selected_ids,
        schedule_enabled=True,
    ).exclude(report_type=SQLReport.ReportType.LEGACY_ARCHIVED)

    processed = 0
    for report in due_reports:
        result, _ = run_sql_report(report)
        if result.error:
            continue

        if (
            not report.schedule_periodic_task_id
            and report.schedule_interval_minutes > 0
        ):
            SQLReport.objects.filter(pk=report.pk).update(
                next_scheduled_run_at=(now or timezone.now())
                + timedelta(minutes=report.schedule_interval_minutes),
                updated_at=timezone.now(),
            )

        processed += 1

    return processed


def _render_pdf_bytes(rendered_html: str) -> bytes:
    """Create a basic PDF document from rendered template text content.

    Parameters:
        rendered_html: Rendered HTML payload.

    Returns:
        PDF bytes.
    """

    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=letter)
    y = 760

    text_content = strip_tags(rendered_html)

    for line in text_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if y <= 60:
            pdf.showPage()
            y = 760

        pdf.drawString(40, y, stripped[:140])
        y -= 14

    pdf.save()
    return stream.getvalue()
