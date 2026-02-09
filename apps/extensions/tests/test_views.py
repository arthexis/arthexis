"""Tests for hosted extension views."""

import pytest
from django.urls import reverse

from apps.extensions.models import JsExtension

pytestmark = pytest.mark.django_db


def test_build_manifest_includes_assets():
    """Ensure manifest generation includes scripts and permissions."""
    extension = JsExtension.objects.create(
        slug="helper",
        name="Helper",
        description="Side task helper",
        version="1.2.3",
        manifest_version=3,
        matches="https://example.com/*",
        content_script="console.log('hi');",
        background_script="console.log('bg');",
        options_page="<html></html>",
        permissions="storage",
        host_permissions="https://example.com/*",
    )

    manifest = extension.build_manifest()

    assert manifest["name"] == "Helper"
    assert manifest["content_scripts"][0]["js"] == ["content.js"]
    assert manifest["background"]["service_worker"] == "background.js"
    assert manifest["options_ui"]["page"] == "options.html"
    assert "storage" in manifest["permissions"]
    assert "https://example.com/*" in manifest["host_permissions"]


def test_build_manifest_mv2_uses_legacy_keys():
    """Ensure MV2 manifests use legacy background and options keys."""
    extension = JsExtension.objects.create(
        slug="mv2-helper",
        name="MV2 Helper",
        manifest_version=2,
        background_script="console.log('bg');",
        options_page="<html></html>",
    )

    manifest = extension.build_manifest()

    assert "browser_action" in manifest
    assert manifest["background"]["scripts"] == ["background.js"]
    assert manifest["background"]["persistent"] is False
    assert manifest["options_page"] == "options.html"


def test_manifest_view_returns_json(client):
    """Return manifest JSON for enabled extensions."""
    extension = JsExtension.objects.create(
        slug="helper",
        name="Helper",
        description="Side task helper",
        version="1.2.3",
        manifest_version=3,
    )

    url = reverse("extensions:manifest", args=[extension.slug])
    response = client.get(url)

    assert response.status_code == 200
    assert response.json()["name"] == "Helper"


def test_content_script_view_returns_js(client):
    """Serve content script assets for enabled extensions."""
    extension = JsExtension.objects.create(
        slug="helper",
        name="Helper",
        content_script="console.log('hi');",
    )

    url = reverse("extensions:content", args=[extension.slug])
    response = client.get(url)

    assert response.status_code == 200
    assert "console.log" in response.content.decode("utf-8")


def test_background_script_view_returns_js(client):
    """Serve background script assets for enabled extensions."""
    extension = JsExtension.objects.create(
        slug="helper",
        name="Helper",
        background_script="console.log('bg');",
    )

    url = reverse("extensions:background", args=[extension.slug])
    response = client.get(url)

    assert response.status_code == 200
    assert "console.log" in response.content.decode("utf-8")


def test_options_page_view_returns_html_with_csp(client):
    """Serve options page HTML with a sandbox CSP."""
    extension = JsExtension.objects.create(
        slug="helper",
        name="Helper",
        options_page="<html></html>",
    )

    url = reverse("extensions:options", args=[extension.slug])
    response = client.get(url)

    assert response.status_code == 200
    assert response["Content-Security-Policy"] == "sandbox allow-scripts"
    assert "<html" in response.content.decode("utf-8")


def test_missing_assets_return_404(client):
    """Return 404 for missing or disabled extension assets."""
    JsExtension.objects.create(
        slug="disabled",
        name="Disabled",
        is_enabled=False,
        content_script="console.log('hi');",
    )
    JsExtension.objects.create(
        slug="no-content",
        name="No Content",
        content_script="",
    )

    response = client.get(reverse("extensions:manifest", args=["disabled"]))
    assert response.status_code == 404

    response = client.get(reverse("extensions:content", args=["no-content"]))
    assert response.status_code == 404
