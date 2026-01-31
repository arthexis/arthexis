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


__all__ = ["SQLReportAdmin"]
