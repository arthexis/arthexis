"""Admin configuration for JS extensions."""

from django.contrib import admin

from apps.extensions.models import JsExtension


@admin.register(JsExtension)
class JsExtensionAdmin(admin.ModelAdmin):
    """Admin configuration for hosted JavaScript extensions."""

    list_display = ("name", "slug", "version", "manifest_version", "is_enabled")
    list_filter = ("is_enabled", "manifest_version")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "name",
                    "slug",
                    "description",
                    "version",
                    "manifest_version",
                    "is_enabled",
                )
            },
        ),
        (
            "Content Scripts",
            {
                "fields": ("matches", "content_script"),
            },
        ),
        (
            "Background",
            {
                "fields": ("background_script",),
            },
        ),
        (
            "Options",
            {
                "fields": ("options_page",),
            },
        ),
        (
            "Permissions",
            {
                "fields": ("permissions", "host_permissions"),
            },
        ),
    )
