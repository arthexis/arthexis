from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from .models import SQLReport


@admin.register(SQLReport)
class SQLReportAdmin(DjangoObjectActions, admin.ModelAdmin):
    change_list_template = "admin/reports/sqlreport/change_list.html"
    changelist_actions = ["open_system_sql_runner"]

    list_display = ("name", "database_alias", "last_run_at", "last_run_duration", "updated_at")
    search_fields = ("name", "query")
    readonly_fields = ("created_at", "updated_at", "last_run_at", "last_run_duration")

    def open_system_sql_runner(self, request, queryset=None):  # pragma: no cover - admin action dispatch
        return HttpResponseRedirect(reverse("admin:system-sql-report"))

    open_system_sql_runner.label = _("Open SQL runner")
    open_system_sql_runner.short_description = _("Open SQL runner")
    open_system_sql_runner.changelist = True
    open_system_sql_runner.requires_queryset = False


__all__ = ["SQLReportAdmin"]
