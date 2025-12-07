from django.contrib import admin

from apps.tests.models import TestResult


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ("node_id", "name", "status", "duration", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("node_id", "name")
    ordering = ("-created_at", "node_id")
    readonly_fields = ("created_at",)
