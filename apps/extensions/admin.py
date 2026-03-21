"""Admin configuration for JS extensions."""

from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

from apps.core.admin.mixins import PublicViewLinksAdminMixin
from apps.extensions.archive import build_extension_archive_response
from apps.extensions.models import JsExtension


@admin.register(JsExtension)
class JsExtensionAdmin(PublicViewLinksAdminMixin, admin.ModelAdmin):
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
    view_on_site = False
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

    def get_view_on_site_url(self, obj=None):
        """Return the public extension catalog or manifest route."""

        if obj is None:
            return reverse("extensions:catalog")
        if not obj.is_enabled:
            return None
        return reverse("extensions:manifest", args=[obj.slug])

    def get_public_view_links(self, obj=None) -> list[dict[str, str]]:
        """Return public extension routes that admins may need to inspect."""

        links = [{"label": "View on site: Catalog", "url": reverse("extensions:catalog")}]
        if obj is None or not obj.is_enabled:
            return links

        links.extend(
            [
                {
                    "label": "View on site: Manifest",
                    "url": reverse("extensions:manifest", args=[obj.slug]),
                },
                {
                    "label": "View on site: Download ZIP",
                    "url": reverse("extensions:download", args=[obj.slug]),
                },
            ]
        )
        if obj.content_script or obj.match_patterns:
            links.append(
                {
                    "label": "View on site: Content script",
                    "url": reverse("extensions:content", args=[obj.slug]),
                }
            )
        if obj.background_script:
            links.append(
                {
                    "label": "View on site: Background script",
                    "url": reverse("extensions:background", args=[obj.slug]),
                }
            )
        if obj.options_page:
            links.append(
                {
                    "label": "View on site: Options page",
                    "url": reverse("extensions:options", args=[obj.slug]),
                }
            )
        return links

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

        return build_extension_archive_response(extension)
