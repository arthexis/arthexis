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
    assert manifest["options_page"] == "options.html"
    assert "storage" in manifest["permissions"]
    assert "https://example.com/*" in manifest["host_permissions"]


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
