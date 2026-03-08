from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from .models import SQLReport, SQLReportProduct


class SQLReportProductInline(admin.TabularInline):
    model = SQLReportProduct
    extra = 0
    can_delete = False
    fields = ("created_at", "database_alias", "row_count", "duration_ms")
    readonly_fields = fields


@admin.register(SQLReport)
class SQLReportAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "database_alias",
        "schedule_enabled",
        "next_scheduled_run_at",
        "last_run_at",
        "last_run_duration",
        "updated_at",
    )
    search_fields = ("name", "query", "html_template_name")
    readonly_fields = ("created_at", "updated_at", "last_run_at", "last_run_duration")
    inlines = [SQLReportProductInline]

    fieldsets = (
        (None, {"fields": ("name", "database_alias", "query")}),
        (
            _("Products"),
            {"fields": ("html_template_name",)},
        ),
        (
            _("Scheduling"),
            {
                "fields": (
                    "schedule_enabled",
                    "schedule_interval_minutes",
                    "next_scheduled_run_at",
                )
            },
        ),
        (
            _("Runtime"),
            {"fields": ("last_run_at", "last_run_duration", "created_at", "updated_at")},
        ),
    )

    changelist_actions = ["open_system"]

    def get_changelist_actions(self, request):  # pragma: no cover - admin hook
        parent = getattr(super(), "get_changelist_actions", None)
        actions = []
        if callable(parent):
            parent_actions = parent(request)
            if parent_actions:
                actions.extend(parent_actions)
        if "open_system" not in actions:
            actions.append("open_system")
        return actions

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "system-sql-report/",
                self.admin_site.admin_view(self.open_system),
                name="reports_sqlreport_open_system",
            )
        ]
        return custom + urls

    def open_system(self, request, queryset=None):
        return HttpResponseRedirect(reverse("admin:system-sql-report"))

    open_system.short_description = _("Run SQL")
    open_system.label = _("Run SQL")
    open_system.requires_queryset = False


@admin.register(SQLReportProduct)
class SQLReportProductAdmin(admin.ModelAdmin):
    list_display = ("report", "created_at", "database_alias", "row_count", "duration_ms")
    list_filter = ("database_alias", "created_at")
    readonly_fields = (
        "report",
        "database_alias",
        "resolved_sql",
        "row_count",
        "duration_ms",
        "html_content",
        "pdf_content",
        "created_at",
    )


__all__ = ["SQLReportAdmin", "SQLReportProductAdmin"]
