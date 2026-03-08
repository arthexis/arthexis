from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from time import perf_counter
from typing import Any

from django.db import DatabaseError, connections
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from .models import SQLReport, SQLReportProduct
from apps.sigils.sigil_resolver import resolve_sigils

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SQLExecutionResult:
    """Structured SQL execution output used by report rendering flows."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    row_count: int
    resolved_sql: str
    database_alias: str
    executed_at: timezone.datetime
    duration_ms: float | None
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        """Return a template/admin friendly representation."""

        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "resolved_sql": self.resolved_sql,
            "database_alias": self.database_alias,
            "executed_at": self.executed_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def execute_sql_report_query(resolved_sql: str, database_alias: str) -> SQLExecutionResult:
    """Execute SQL against ``database_alias`` and return query metadata and rows."""

    executed_at = timezone.now()
    alias = database_alias if database_alias in connections else "default"

    result = SQLExecutionResult(
        columns=[],
        rows=[],
        row_count=0,
        resolved_sql=resolved_sql,
        database_alias=alias,
        executed_at=executed_at,
        duration_ms=None,
        error=None,
    )

    try:
        with connections[alias].cursor() as cursor:
            started = perf_counter()
            cursor.execute(resolved_sql)
            duration_seconds = perf_counter() - started
            result.duration_ms = duration_seconds * 1000

            if cursor.description:
                result.columns = [col[0] for col in cursor.description]
                result.rows = cursor.fetchall()
                result.row_count = len(result.rows)
            elif cursor.rowcount and cursor.rowcount > 0:
                result.row_count = cursor.rowcount
    except DatabaseError as exc:
        result.error = str(exc)
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected error executing SQL report")
        result.error = str(exc)

    return result


def render_report_product(sql_report: SQLReport, result: SQLExecutionResult) -> SQLReportProduct:
    """Render and persist HTML/PDF outputs for a SQL report execution."""

    context = {
        "report": sql_report,
        "query_result": result.as_dict(),
    }
    html_content = render_to_string(sql_report.html_template_name, context)
    pdf_bytes = _render_pdf_bytes(html_content)

    return SQLReportProduct.objects.create(
        report=sql_report,
        database_alias=result.database_alias,
        resolved_sql=result.resolved_sql,
        row_count=result.row_count,
        duration_ms=result.duration_ms,
        html_content=html_content,
        pdf_content=pdf_bytes,
    )


def run_sql_report(sql_report: SQLReport) -> tuple[SQLExecutionResult, SQLReportProduct | None]:
    """Resolve sigils, execute SQL, update run metadata, and render products."""

    resolved_sql = resolve_sigils(sql_report.query)
    result = execute_sql_report_query(resolved_sql, sql_report.database_alias)

    if result.error:
        return result, None

    SQLReport.objects.filter(pk=sql_report.pk).update(
        last_run_at=result.executed_at,
        last_run_duration=timedelta(milliseconds=result.duration_ms or 0),
        updated_at=timezone.now(),
    )
    sql_report.refresh_from_db(fields=["last_run_at", "last_run_duration"])

    product = render_report_product(sql_report, result)
    return result, product


def run_due_scheduled_reports(now: timezone.datetime | None = None) -> int:
    """Run all enabled reports whose schedule indicates they are due."""

    current = now or timezone.now()
    due_reports = SQLReport.objects.filter(
        schedule_enabled=True,
        schedule_interval_minutes__gt=0,
        next_scheduled_run_at__lte=current,
    )

    processed = 0
    for report in due_reports:
        result, _ = run_sql_report(report)
        if result.error:
            continue

        report.next_scheduled_run_at = current + timedelta(minutes=report.schedule_interval_minutes)
        report.save(update_fields=["next_scheduled_run_at", "updated_at"])
        processed += 1

    return processed


def _render_pdf_bytes(rendered_html: str) -> bytes:
    """Create a basic PDF document from rendered template text content."""

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
