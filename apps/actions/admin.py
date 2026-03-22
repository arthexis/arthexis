"""Admin integrations for supported actions configuration."""

from __future__ import annotations

from django.contrib import admin

from apps.actions.models import DashboardAction, StaffTask, StaffTaskPreference
from apps.locals.user_data import EntityModelAdmin


@admin.register(StaffTask)
class StaffTaskAdmin(EntityModelAdmin):
    """Manage available dashboard task panels and default visibility."""

    list_display = (
        "label",
        "slug",
        "action_name",
        "order",
        "default_enabled",
        "superuser_only",
        "is_active",
    )
    list_filter = ("action_name", "default_enabled", "superuser_only", "is_active")
    search_fields = ("label", "slug", "action_name", "description")


@admin.register(StaffTaskPreference)
class StaffTaskPreferenceAdmin(EntityModelAdmin):
    """Inspect per-user task panel visibility overrides."""

    list_display = ("user", "task", "is_enabled", "updated_at")
    list_filter = ("is_enabled", "task")
    search_fields = ("user__username", "task__label", "task__slug")


@admin.register(DashboardAction)
class DashboardActionAdmin(EntityModelAdmin):
    """Manage named admin-dashboard links for model rows."""

    list_display = (
        "display_label",
        "content_type",
        "action_name",
        "is_active",
        "order",
    )
    list_filter = ("action_name", "is_active", "content_type__app_label")
    search_fields = ("label", "slug", "action_name")
