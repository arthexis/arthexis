from django.contrib import admin

from .models import LLMSummaryConfig


@admin.register(LLMSummaryConfig)
class LLMSummaryConfigAdmin(admin.ModelAdmin):
    list_display = ("display", "slug", "is_active", "installed_at", "last_run_at")
    list_filter = ("is_active",)
    search_fields = ("slug", "display")
    readonly_fields = ("installed_at", "last_run_at", "created_at", "updated_at")
