from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from .models import SQLReport


@admin.register(SQLReport)
class SQLReportAdmin(admin.ModelAdmin):
    list_display = ("name", "database_alias", "last_run_at", "last_run_duration", "updated_at")
    search_fields = ("name", "query")
    readonly_fields = ("created_at", "updated_at", "last_run_at", "last_run_duration")

    changelist_actions = ["open_system_sql_report"]

    def get_changelist_actions(self, request):  # pragma: no cover - admin hook
        parent = getattr(super(), "get_changelist_actions", None)
        actions = []
        if callable(parent):
            parent_actions = parent(request)
            if parent_actions:
                actions.extend(parent_actions)
        if "open_system_sql_report" not in actions:
            actions.append("open_system_sql_report")
        return actions

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "system-sql-report/",
                self.admin_site.admin_view(self.open_system_sql_report),
                name="reports_sqlreport_open_system_sql_report",
            )
        ]
        return custom + urls

    def open_system_sql_report(self, request, queryset=None):
        return HttpResponseRedirect(reverse("admin:system-sql-report"))

    open_system_sql_report.short_description = _("System SQL report")
    open_system_sql_report.label = _("System SQL report")
    open_system_sql_report.requires_queryset = False


__all__ = ["SQLReportAdmin"]
