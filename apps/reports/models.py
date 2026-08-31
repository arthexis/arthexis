from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class SQLReport(Entity):
    """Saved named reports backed by approved code implementations.

    Parameters:
        None.

    Returns:
        ``SQLReport`` model instance.
    """

    class ReportType(models.TextChoices):
        REPORT_PRODUCT_ACTIVITY = "report_product_activity", _("Report product activity")
        SCHEDULED_REPORTS = "scheduled_reports", _("Scheduled reports overview")
        SIGIL_ROOTS = "sigil_roots", _("Sigil roots catalog")
        LEGACY_ARCHIVED = "legacy_archived", _("Archived legacy SQL report")

    name = models.CharField(max_length=255, unique=True)
    report_type = models.CharField(max_length=64, choices=ReportType.choices)
    parameters = models.JSONField(default=dict, blank=True)
    database_alias = models.CharField(max_length=128, default="default", editable=False)
    query = models.TextField(blank=True, default="", editable=False)
    html_template_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        editable=False,
    )
    legacy_definition = models.JSONField(blank=True, null=True, editable=False)
    schedule_enabled = models.BooleanField(default=False)
    schedule_interval_minutes = models.PositiveIntegerField(default=0)
    schedule_crontab = models.ForeignKey(
        "django_celery_beat.CrontabSchedule",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="scheduled_sql_reports",
    )
    schedule_interval = models.ForeignKey(
        "django_celery_beat.IntervalSchedule",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="scheduled_sql_reports",
    )
    schedule_periodic_task = models.OneToOneField(
        "django_celery_beat.PeriodicTask",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="scheduled_sql_report",
    )
    next_scheduled_run_at = models.DateTimeField(blank=True, null=True)
    last_run_at = models.DateTimeField(blank=True, null=True)
    last_run_duration = models.DurationField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_sqlreport"
        ordering = ("name",)
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")

    def __str__(self) -> str:  # pragma: no cover - human-readable representation
        return self.name

    @property
    def definition(self):
        """Return the maintained definition for this report type.

        Parameters:
            None.

        Returns:
            Matching ``ReportDefinition`` instance.

        Raises:
            ValidationError: If the report type is archived or unknown.
        """

        from .report_definitions import get_report_definition

        return get_report_definition(self.report_type)

    def clean(self) -> None:
        """Validate report type, parameters, and schedule configuration.

        Parameters:
            None.

        Returns:
            None.

        Raises:
            ValidationError: If report configuration is invalid.
        """

        super().clean()
        errors: dict[str, ValidationError | str] = {}

        if not isinstance(self.parameters, dict):
            errors["parameters"] = _("Parameters must be a JSON object.")
        elif self.report_type == self.ReportType.LEGACY_ARCHIVED:
            self.parameters = {}
        else:
            try:
                self.parameters = self.definition.clean_parameters(self.parameters)
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    errors.update(exc.message_dict)
                else:
                    errors["parameters"] = exc

        if self.report_type == self.ReportType.LEGACY_ARCHIVED and not self.legacy_definition:
            errors["legacy_definition"] = _(
                "Archived legacy SQL reports must preserve the original definition."
            )

        if self.schedule_enabled:
            cadence_count = int(self.schedule_interval is not None) + int(
                self.schedule_crontab is not None
            )
            if cadence_count > 1:
                errors["schedule_interval"] = _(
                    "Select either an interval or a crontab cadence, not both."
                )
            elif cadence_count == 0 and self.schedule_interval_minutes <= 0:
                errors["schedule_interval_minutes"] = _(
                    "Set a positive interval or choose an interval/crontab cadence when scheduling is enabled."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate configuration before saving the report.

        Parameters:
            *args: Positional ``save`` arguments.
            **kwargs: Keyword ``save`` arguments.

        Returns:
            None.

        Raises:
            ValidationError: If validation fails.
        """

        self.full_clean()
        super().save(*args, **kwargs)
        from .scheduling import sync_report_schedule

        sync_report_schedule(self)

    def record_last_run(self, started_at, runtime_seconds: float) -> None:
        """Persist the timestamp and duration for the latest successful run.

        Parameters:
            started_at: Execution start time.
            runtime_seconds: Execution duration in seconds.

        Returns:
            None.
        """

        self.last_run_at = started_at
        self.last_run_duration = timedelta(seconds=runtime_seconds)
        self.save(update_fields=["last_run_at", "last_run_duration", "updated_at"])


class SQLReportProduct(Entity):
    """Rendered outputs produced after a named report execution."""

    FORMAT_HTML = "html"
    FORMAT_PDF = "pdf"

    report = models.ForeignKey(
        SQLReport,
        on_delete=models.CASCADE,
        related_name="products",
    )
    report_type = models.CharField(max_length=64)
    parameters = models.JSONField(default=dict, blank=True)
    renderer_template_name = models.CharField(max_length=255)
    execution_details = models.JSONField(default=dict, blank=True)
    database_alias = models.CharField(max_length=128, blank=True, default="", editable=False)
    resolved_sql = models.TextField(blank=True, default="", editable=False)
    row_count = models.PositiveIntegerField(default=0)
    duration_ms = models.FloatField(blank=True, null=True)
    html_content = models.TextField()
    pdf_content = models.BinaryField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("Report Product")
        verbose_name_plural = _("Report Products")


__all__ = ["SQLReport", "SQLReportProduct"]
