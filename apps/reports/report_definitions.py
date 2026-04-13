from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as datetime_timezone
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.reports.models import SQLReport, SQLReportProduct
from apps.sigils.models import SigilRoot


@dataclass(frozen=True, slots=True)
class ReportExecution:
    """Structured report execution payload used by renderers and products.

    Parameters:
        columns: Ordered table column labels.
        rows: Tabular row data.
        row_count: Number of rows returned.
        executed_at: Timestamp for the execution.
        duration_ms: Execution duration in milliseconds.
        details: Additional renderer-safe metadata.

    Returns:
        ReportExecution instance.
    """

    columns: list[str]
    rows: list[tuple[Any, ...]]
    row_count: int
    executed_at: datetime
    duration_ms: float | None
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a template-friendly representation.

        Parameters:
            None.

        Returns:
            Serialized execution payload for templates and admin views.
        """

        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "executed_at": self.executed_at,
            "duration_ms": self.duration_ms,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class ReportDefinition:
    """Named report implementation with explicit validation and rendering.

    Parameters:
        key: Stable report identifier stored on ``SQLReport``.
        label: Human-readable name for admin forms.
        description: Report description shown in admin help text.
        template_name: Maintained template path used for HTML/PDF rendering.
        default_parameters: Default parameter payload.

    Returns:
        ReportDefinition instance.
    """

    key: str
    label: str
    description: str
    template_name: str
    default_parameters: dict[str, Any]

    def clean_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize report parameters.

        Parameters:
            parameters: Raw parameter payload stored on the report model.

        Returns:
            Normalized parameters dictionary.

        Raises:
            ValidationError: If the supplied parameters are invalid.
        """

        raise NotImplementedError

    def execute(self, parameters: dict[str, Any]) -> ReportExecution:
        """Run the report implementation and return structured rows.

        Parameters:
            parameters: Validated parameters for the report execution.

        Returns:
            Structured report output.
        """

        raise NotImplementedError


class SigilRootsReportDefinition(ReportDefinition):
    """Catalog configured sigil roots and their backing models."""

    def clean_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        normalized = {"context_type": str(parameters.get("context_type") or "all").strip().lower()}
        allowed = {"all", *[value for value, _ in SigilRoot.Context.choices]}
        if normalized["context_type"] not in allowed:
            raise ValidationError({"parameters": _("Select a valid sigil context filter.")})
        return normalized

    def execute(self, parameters: dict[str, Any]) -> ReportExecution:
        started = timezone.now()
        queryset = SigilRoot.objects.select_related("content_type").order_by("prefix")
        context_filter = parameters["context_type"]
        if context_filter != "all":
            queryset = queryset.filter(context_type=context_filter)

        rows = [
            (
                root.prefix,
                root.get_context_type_display(),
                root.content_type.app_label if root.content_type else "",
                root.content_type.model if root.content_type else "",
            )
            for root in queryset
        ]
        finished = timezone.now()
        return ReportExecution(
            columns=["Prefix", "Context", "App", "Model"],
            rows=rows,
            row_count=len(rows),
            executed_at=finished,
            duration_ms=(finished - started).total_seconds() * 1000,
            details={"context_type": context_filter},
        )


class ReportProductActivityDefinition(ReportDefinition):
    """Catalog previously rendered report products with safe ORM filters."""

    def clean_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        name_contains = str(parameters.get("report_name_contains") or "").strip()
        created_since_raw = str(parameters.get("created_since") or "").strip()
        limit_raw = parameters.get("limit", 50)
        errors: dict[str, list[str]] = {}

        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            errors.setdefault("parameters", []).append(str(_("Limit must be an integer.")))
            limit = 50

        if not 1 <= limit <= 200:
            errors.setdefault("parameters", []).append(
                str(_("Limit must be between 1 and 200."))
            )

        created_since = None
        if created_since_raw:
            try:
                created_since = datetime.fromisoformat(created_since_raw)
            except ValueError as exc:
                raise ValidationError(
                    {"parameters": _("created_since must be a valid ISO-8601 datetime.")}
                ) from exc
            if timezone.is_naive(created_since):
                created_since = timezone.make_aware(created_since, datetime_timezone.utc)

        if errors:
            raise ValidationError(errors)

        return {
            "report_name_contains": name_contains,
            "created_since": created_since.isoformat() if created_since else "",
            "limit": limit,
        }

    def execute(self, parameters: dict[str, Any]) -> ReportExecution:
        started = timezone.now()
        queryset = SQLReportProduct.objects.select_related("report").order_by("-created_at")
        if parameters["report_name_contains"]:
            queryset = queryset.filter(report__name__icontains=parameters["report_name_contains"])
        if parameters["created_since"]:
            queryset = queryset.filter(created_at__gte=datetime.fromisoformat(parameters["created_since"]))
        queryset = queryset[: parameters["limit"]]

        rows = [
            (
                product.report.name,
                product.report.get_report_type_display(),
                product.created_at,
                product.row_count,
                product.duration_ms,
                product.renderer_template_name,
            )
            for product in queryset
        ]
        finished = timezone.now()
        return ReportExecution(
            columns=["Report", "Type", "Created", "Rows", "Duration (ms)", "Template"],
            rows=rows,
            row_count=len(rows),
            executed_at=finished,
            duration_ms=(finished - started).total_seconds() * 1000,
            details={
                "report_name_contains": parameters["report_name_contains"],
                "created_since": parameters["created_since"],
                "limit": parameters["limit"],
            },
        )


class ScheduledReportsDefinition(ReportDefinition):
    """Catalog report schedule status using approved ORM filters."""

    def clean_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        schedule_state = str(parameters.get("schedule_state") or "all").strip().lower()
        name_contains = str(parameters.get("name_contains") or "").strip()
        if schedule_state not in {"all", "enabled", "due"}:
            raise ValidationError({"parameters": _("Select a valid schedule state filter.")})
        return {
            "schedule_state": schedule_state,
            "name_contains": name_contains,
        }

    def execute(self, parameters: dict[str, Any]) -> ReportExecution:
        started = timezone.now()
        now = timezone.now()
        queryset = SQLReport.objects.order_by("name")
        if parameters["name_contains"]:
            queryset = queryset.filter(name__icontains=parameters["name_contains"])
        if parameters["schedule_state"] == "enabled":
            queryset = queryset.filter(schedule_enabled=True)
        elif parameters["schedule_state"] == "due":
            queryset = queryset.filter(
                Q(
                    schedule_enabled=True,
                    schedule_periodic_task__enabled=True,
                )
                | Q(
                    schedule_enabled=True,
                    schedule_interval_minutes__gt=0,
                    next_scheduled_run_at__lte=now,
                )
            )

        rows = [
            (
                report.name,
                report.get_report_type_display(),
                report.schedule_enabled,
                report.schedule_interval_minutes,
                report.next_scheduled_run_at,
                report.last_run_at,
            )
            for report in queryset
        ]
        finished = timezone.now()
        return ReportExecution(
            columns=["Name", "Type", "Scheduled", "Interval", "Next run", "Last run"],
            rows=rows,
            row_count=len(rows),
            executed_at=finished,
            duration_ms=(finished - started).total_seconds() * 1000,
            details={"schedule_state": parameters["schedule_state"], "name_contains": parameters["name_contains"]},
        )


REPORT_DEFINITIONS: tuple[ReportDefinition, ...] = (
    ReportProductActivityDefinition(
        key="report_product_activity",
        label=str(_("Report product activity")),
        description=str(_("Catalog generated report products with safe ORM filters.")),
        template_name="reports/sql/report_product_activity.html",
        default_parameters={"report_name_contains": "", "created_since": "", "limit": 50},
    ),
    ScheduledReportsDefinition(
        key="scheduled_reports",
        label=str(_("Scheduled reports overview")),
        description=str(_("List report schedules and due state without raw SQL.")),
        template_name="reports/sql/scheduled_reports.html",
        default_parameters={"schedule_state": "all", "name_contains": ""},
    ),
    SigilRootsReportDefinition(
        key="sigil_roots",
        label=str(_("Sigil roots catalog")),
        description=str(_("Catalog configured sigil roots and mapped content types.")),
        template_name="reports/sql/sigil_roots.html",
        default_parameters={"context_type": "all"},
    ),
)
REPORT_DEFINITION_MAP = {definition.key: definition for definition in REPORT_DEFINITIONS}
LEGACY_REPORT_TYPE = "legacy_archived"


def get_report_definition(report_type: str) -> ReportDefinition:
    """Return the configured report definition for ``report_type``.

    Parameters:
        report_type: Stored report type key.

    Returns:
        The matching ``ReportDefinition``.

    Raises:
        ValidationError: If the report type is unknown or archived.
    """

    if report_type == LEGACY_REPORT_TYPE:
        raise ValidationError({"report_type": _("Archived legacy SQL reports cannot be executed.")})
    try:
        return REPORT_DEFINITION_MAP[report_type]
    except KeyError as exc:
        raise ValidationError({"report_type": _("Select a valid report type.")}) from exc


def report_type_choices() -> list[tuple[str, str]]:
    """Return admin/model choices for named report implementations.

    Parameters:
        None.

    Returns:
        Stable report type choices.
    """

    return [(definition.key, definition.label) for definition in REPORT_DEFINITIONS] + [
        (LEGACY_REPORT_TYPE, str(_("Archived legacy SQL report")))
    ]


def report_catalog() -> list[dict[str, Any]]:
    """Return the maintained catalog of shipped report implementations.

    Parameters:
        None.

    Returns:
        A list of report metadata dictionaries.
    """

    return [
        {
            "key": definition.key,
            "label": definition.label,
            "description": definition.description,
            "template_name": definition.template_name,
            "default_parameters": definition.default_parameters,
        }
        for definition in REPORT_DEFINITIONS
    ]
