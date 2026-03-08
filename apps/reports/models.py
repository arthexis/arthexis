from datetime import timedelta

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class SQLReport(Entity):
    """Saved SQL queries that can be re-run from the System reports area."""

    name = models.CharField(max_length=255, unique=True)
    database_alias = models.CharField(max_length=128, default="default")
    query = models.TextField()
    html_template_name = models.CharField(
        max_length=255,
        default="reports/sql/default_report.html",
        help_text=_("Template used for HTML and PDF product rendering."),
    )
    schedule_enabled = models.BooleanField(default=False)
    schedule_interval_minutes = models.PositiveIntegerField(default=0)
    next_scheduled_run_at = models.DateTimeField(blank=True, null=True)
    last_run_at = models.DateTimeField(blank=True, null=True)
    last_run_duration = models.DurationField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_sqlreport"
        ordering = ("name",)
        verbose_name = _("SQL Report")
        verbose_name_plural = _("SQL Reports")

    def __str__(self) -> str:  # pragma: no cover - human-readable representation
        return self.name

    def record_last_run(self, started_at, runtime_seconds: float) -> None:
        self.last_run_at = started_at
        self.last_run_duration = timedelta(seconds=runtime_seconds)
        self.save(update_fields=["last_run_at", "last_run_duration", "updated_at"])


class SQLReportProduct(Entity):
    """Rendered outputs produced after a SQL report execution."""

    FORMAT_HTML = "html"
    FORMAT_PDF = "pdf"

    report = models.ForeignKey(
        SQLReport,
        on_delete=models.CASCADE,
        related_name="products",
    )
    database_alias = models.CharField(max_length=128)
    resolved_sql = models.TextField()
    row_count = models.PositiveIntegerField(default=0)
    duration_ms = models.FloatField(blank=True, null=True)
    html_content = models.TextField()
    pdf_content = models.BinaryField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("SQL Report Product")
        verbose_name_plural = _("SQL Report Products")


__all__ = ["SQLReport", "SQLReportProduct"]
