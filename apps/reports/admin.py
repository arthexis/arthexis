from django.contrib import admin

from .models import SQLReport


@admin.register(SQLReport)
class SQLReportAdmin(admin.ModelAdmin):
    list_display = ("name", "database_alias", "last_run_at", "last_run_duration", "updated_at")
    search_fields = ("name", "query")
    readonly_fields = ("created_at", "updated_at", "last_run_at", "last_run_duration")


__all__ = ["SQLReportAdmin"]
