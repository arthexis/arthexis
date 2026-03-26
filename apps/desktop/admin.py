"""Admin registration for desktop shortcut models."""

from __future__ import annotations

from django.contrib import admin

from apps.desktop.models import DesktopShortcut


@admin.register(DesktopShortcut)
class DesktopShortcutAdmin(admin.ModelAdmin):
    """Admin configuration for model-driven desktop shortcuts."""

    list_display = (
        "name",
        "slug",
        "desktop_filename",
        "install_location",
        "is_enabled",
        "require_desktop_ui",
        "sort_order",
    )
    list_filter = (
        "install_location",
        "is_enabled",
        "require_desktop_ui",
        "only_staff",
        "only_superuser",
    )
    search_fields = ("name", "slug", "desktop_filename", "comment", "target_url")
    filter_horizontal = ("required_features", "required_groups")

    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "name",
                    "slug",
                    "desktop_filename",
                    "comment",
                    "sort_order",
                    "is_enabled",
                )
            },
        ),
        (
            "Launch",
            {
                "description": "Desktop shortcuts always open an HTTP(S) URL via the browser helper.",
                "fields": (
                    "install_location",
                    "target_url",
                    "terminal",
                    "categories",
                    "startup_notify",
                )
            },
        ),
        (
            "Icon",
            {
                "fields": (
                    "icon_name",
                    "icon_base64",
                    "icon_extension",
                )
            },
        ),
        (
            "Conditions",
            {
                "description": (
                    "Shortcuts install only when all selected structured conditions "
                    "pass for the target user. The optional expression is limited "
                    "to the documented shortcut context values."
                ),
                "fields": (
                    "require_desktop_ui",
                    "required_features",
                    "required_groups",
                    "only_staff",
                    "only_superuser",
                    "condition_expression",
                ),
            },
        ),
        (
            "Extended desktop entry",
            {
                "fields": ("extra_entries",),
            },
        ),
    )
