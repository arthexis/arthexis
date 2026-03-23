"""Admin registration for desktop assistant models."""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpRequest

from apps.desktop.models import DesktopShortcut, RegisteredExtension
from apps.desktop.services import register_extension_with_os


@admin.register(RegisteredExtension)
class RegisteredExtensionAdmin(admin.ModelAdmin):
    """Admin configuration for operating system extension registrations."""

    list_display = (
        "extension",
        "django_command",
        "filename_sigil",
        "filename_as_input",
        "is_enabled",
    )
    search_fields = ("extension", "description", "django_command", "extra_args")
    list_filter = ("is_enabled", "filename_as_input")
    actions = ("register_selected_extensions",)

    fieldsets = (
        (
            "Extension Mapping",
            {
                "fields": (
                    "extension",
                    "description",
                    "is_enabled",
                )
            },
        ),
        (
            "Executable",
            {
                "description": (
                    "Configure the Django command and optional arguments used to open "
                    "files for this extension. Use the filename sigil to inject the "
                    "opened filename in arguments, or enable input mode to pass the "
                    "filename through stdin."
                ),
                "fields": (
                    "django_command",
                    "extra_args",
                    "filename_sigil",
                    "filename_as_input",
                ),
            },
        ),
    )

    @admin.action(description="Register selected extensions")
    def register_selected_extensions(self, request: HttpRequest, queryset):
        """Register the selected extension mappings with the operating system."""

        for registered_extension in queryset:
            result = register_extension_with_os(registered_extension)
            level = messages.SUCCESS if result.success else messages.ERROR
            self.message_user(
                request,
                f"{registered_extension.extension}: {result.message}",
                level=level,
            )


@admin.register(DesktopShortcut)
class DesktopShortcutAdmin(admin.ModelAdmin):
    """Admin configuration for model-driven desktop shortcuts."""

    list_display = (
        "name",
        "slug",
        "desktop_filename",
        "launch_mode",
        "install_location",
        "is_enabled",
        "require_desktop_ui",
        "sort_order",
    )
    list_filter = (
        "launch_mode",
        "install_location",
        "is_enabled",
        "require_desktop_ui",
        "only_staff",
        "only_superuser",
    )
    search_fields = ("name", "slug", "desktop_filename", "comment", "target_url", "command")
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
                "fields": (
                    "launch_mode",
                    "install_location",
                    "target_url",
                    "command",
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
                    "Shortcuts install only when all selected conditions pass for "
                    "the target user."
                ),
                "fields": (
                    "require_desktop_ui",
                    "required_features",
                    "required_groups",
                    "only_staff",
                    "only_superuser",
                    "condition_expression",
                    "condition_command",
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
