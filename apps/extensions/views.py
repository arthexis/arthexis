"""Views for serving hosted extension assets."""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from apps.extensions.models import JsExtension


def _get_enabled_extension(slug: str) -> JsExtension:
    """Return the enabled extension matching the given slug."""
    return get_object_or_404(JsExtension, slug=slug, is_enabled=True)


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
    return HttpResponse(extension.background_script, content_type="application/javascript")


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
