from __future__ import annotations

from django.contrib import admin

from .models import LifecycleService


@admin.register(LifecycleService)
class LifecycleServiceAdmin(admin.ModelAdmin):
    list_display = (
        "display",
        "slug",
        "unit_template",
        "activation",
        "feature_slug",
        "sort_order",
    )
    list_filter = ("activation",)
    search_fields = ("display", "slug", "unit_template", "feature_slug")
    ordering = ("sort_order", "display")
