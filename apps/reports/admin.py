from django import forms
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _

from .models import SQLReport, SQLReportProduct
from .report_definitions import report_catalog
from .services import run_sql_report


class SQLReportAdminForm(forms.ModelForm):
    """Admin form that validates named report parameters."""

    class Meta:
        model = SQLReport
        fields = "__all__"
        widgets = {
            "parameters": forms.Textarea(attrs={"rows": 8, "class": "vLargeTextField"}),
        }


class SQLReportProductInline(admin.TabularInline):
    model = SQLReportProduct
    extra = 0
    can_delete = False
    fields = ("created_at", "report_type", "row_count", "duration_ms", "renderer_template_name")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):  # pragma: no cover - admin hook
        return False


@admin.register(SQLReport)
class SQLReportAdmin(admin.ModelAdmin):
    form = SQLReportAdminForm
    list_display = (
        "name",
        "report_type",
        "schedule_enabled",
        "schedule_periodic_task",
        "next_scheduled_run_at",
        "last_run_at",
        "last_run_duration",
        "updated_at",
    )
    list_filter = ("report_type", "schedule_enabled")
    search_fields = ("name", "report_type")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_run_at",
        "last_run_duration",
        "maintained_report_catalog",
        "legacy_definition",
        "schedule_periodic_task",
    )
    inlines = [SQLReportProductInline]
    actions = ["run_selected_reports"]

    fieldsets = (
        (None, {"fields": ("name", "report_type", "parameters")}),
        (
            _("Maintained catalog"),
            {"fields": ("maintained_report_catalog", "legacy_definition")},
        ),
        (
            _("Scheduling"),
            {
                "fields": (
                    "schedule_enabled",
                    "schedule_interval_minutes",
                    "schedule_interval",
                    "schedule_crontab",
                    "schedule_periodic_task",
                    "next_scheduled_run_at",
                )
            },
        ),
        (
            _("Runtime"),
            {"fields": ("last_run_at", "last_run_duration", "created_at", "updated_at")},
        ),
    )

    def maintained_report_catalog(self, obj: SQLReport | None = None) -> str:
        """Render the shipped report catalog for administrators."""

        return format_html_join(
            "",
            "<div><strong>{}</strong> <code>{}</code><br><span>{}</span><br><small>Template: {}</small><br><small>Defaults: {}</small></div><br>",
            [
                (
                    item["label"],
                    item["key"],
                    item["description"],
                    item["template_name"],
                    item["default_parameters"],
                )
                for item in report_catalog()
            ],
        )

    maintained_report_catalog.short_description = _("Shipped reports")

    @admin.action(description=_("Run selected reports"))
    def run_selected_reports(self, request: HttpRequest, queryset: QuerySet[SQLReport]) -> None:
        """Execute selected reports and report success or failure in admin."""

        successes = 0
        failures: list[str] = []
        for report in queryset:
            result, product = run_sql_report(report)
            if result.error:
                failures.append(f"{report.name}: {result.error}")
                continue
            successes += 1
            if product is not None:
                messages.success(
                    request,
                    _("%(name)s ran successfully and produced product %(product_id)s.")
                    % {"name": report.name, "product_id": product.pk},
                )

        if failures:
            messages.warning(request, " | ".join(failures))
        if not successes and not failures:
            messages.info(request, _("No reports were selected."))


@admin.register(SQLReportProduct)
class SQLReportProductAdmin(admin.ModelAdmin):
    list_display = (
        "report",
        "report_type",
        "created_at",
        "row_count",
        "duration_ms",
        "renderer_template_name",
    )
    list_filter = ("report_type", "created_at")
    readonly_fields = (
        "report",
        "report_type",
        "parameters",
        "renderer_template_name",
        "execution_details",
        "row_count",
        "duration_ms",
        "html_content",
        "pdf_content",
        "created_at",
    )

    def has_add_permission(self, request):  # pragma: no cover - admin hook
        return False

    def has_delete_permission(self, request, obj=None):  # pragma: no cover - admin hook
        return False


__all__ = ["SQLReportAdmin", "SQLReportProductAdmin"]
