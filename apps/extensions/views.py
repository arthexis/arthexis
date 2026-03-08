"""Views for serving hosted extension assets."""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from apps.extensions.archive import build_extension_archive_response
from apps.extensions.models import JsExtension


def _get_enabled_extension(slug: str) -> JsExtension:
    """Return the enabled extension matching the given slug."""
    return get_object_or_404(JsExtension, slug=slug, is_enabled=True)


def extension_catalog(request: HttpRequest) -> JsonResponse:
    """Return metadata for all enabled extensions."""
    payload = [
        {
            "slug": extension.slug,
            "name": extension.name,
            "description": extension.description,
            "version": extension.version,
            "manifest_version": extension.manifest_version,
            "manifest_url": request.build_absolute_uri(
                reverse("extensions:manifest", args=[extension.slug])
            ),
            "download_url": request.build_absolute_uri(
                reverse("extensions:download", args=[extension.slug])
            ),
        }
        for extension in JsExtension.objects.filter(is_enabled=True).order_by("name")
    ]
    return JsonResponse({"extensions": payload})


def extension_download_archive(request: HttpRequest, slug: str) -> HttpResponse:
    """Return a ZIP archive containing extension files for installation."""
    extension = _get_enabled_extension(slug)
    return build_extension_archive_response(extension)


def extension_manifest(request: HttpRequest, slug: str) -> JsonResponse:
    """Serve the manifest.json for a hosted extension."""
    extension = _get_enabled_extension(slug)
    return JsonResponse(extension.build_manifest())


def extension_content_script(request: HttpRequest, slug: str) -> HttpResponse:
    """Serve the content script for a hosted extension."""
    extension = _get_enabled_extension(slug)
    if not extension.content_script and not extension.match_patterns:
        raise Http404("Content script not available.")
    return HttpResponse(
        extension.build_content_script_payload(),
        content_type="application/javascript",
    )


def extension_background_script(request: HttpRequest, slug: str) -> HttpResponse:
    """Serve the background script for a hosted extension."""
    extension = _get_enabled_extension(slug)
    if not extension.background_script:
        raise Http404("Background script not available.")
    return HttpResponse(
        extension.background_script, content_type="application/javascript"
    )


def extension_options_page(request: HttpRequest, slug: str) -> HttpResponse:
    """Serve the options page HTML for a hosted extension."""
    extension = _get_enabled_extension(slug)
    if not extension.options_page:
        raise Http404("Options page not available.")
    return HttpResponse(
        extension.options_page,
        content_type="text/html",
        headers={"Content-Security-Policy": "sandbox allow-scripts"},
    )
