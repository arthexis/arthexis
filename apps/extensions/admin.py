"""Admin configuration for JS extensions."""

from __future__ import annotations

import io
import json
import re
import zipfile

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

from apps.extensions.models import JsExtension


@admin.register(JsExtension)
class JsExtensionAdmin(admin.ModelAdmin):
    """Admin configuration for hosted JavaScript extensions."""

    list_display = (
        "name",
        "slug",
        "version",
        "manifest_version",
        "is_enabled",
        "download_archive_link",
    )
    list_filter = ("is_enabled", "manifest_version")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("download_archive_link",)
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
                    "download_archive_link",
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

    def get_urls(self):
        """Register custom admin endpoints for extension downloads."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/download/",
                self.admin_site.admin_view(self.download_archive_view),
                name="extensions_jsextension_download",
            )
        ]
        return custom_urls + urls

    @admin.display(description="Download")
    def download_archive_link(self, obj: JsExtension) -> str:
        """Render a direct download link for the extension archive."""
        if not obj.pk:
            return "Save and continue editing to enable download."
        url = reverse("admin:extensions_jsextension_download", args=[obj.pk])
        return format_html('<a href="{}">Download ZIP</a>', url)

    def download_archive_view(
        self, request: HttpRequest, object_id: str
    ) -> HttpResponse:
        """Return a ZIP archive containing extension files for installation."""
        extension = get_object_or_404(JsExtension, pk=object_id)
        if not self.has_view_or_change_permission(request, extension):
            raise PermissionDenied

        archive = io.BytesIO()
        with zipfile.ZipFile(
            archive, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as bundle:
            for filename, contents in extension.build_extension_archive_files().items():
                payload = (
                    json.dumps(contents, indent=2)
                    if isinstance(contents, dict)
                    else str(contents)
                )
                bundle.writestr(filename, payload)

        archive.seek(0)
        response = HttpResponse(archive.getvalue(), content_type="application/zip")

        raw_name = f"{extension.slug}-{extension.version}.zip"
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._")
        if not safe_name:
            safe_name = "extension.zip"
        response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        return response
