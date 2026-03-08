"""Tests for hosted extension views."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.urls import reverse

from apps.extensions.models import JsExtension

pytestmark = pytest.mark.django_db


def test_build_manifest_includes_assets() -> None:
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


def test_build_manifest_includes_bootstrap_content_script_when_matches_exist() -> None:
    """Ensure matches generate content script registration even without custom JS."""
    extension = JsExtension.objects.create(
        slug="bootstrap-only",
        name="Bootstrap Only",
        matches="https://example.com/*",
        content_script="",
    )

    manifest = extension.build_manifest()

    assert manifest["content_scripts"][0]["js"] == ["content.js"]


def test_build_manifest_mv2_uses_legacy_keys() -> None:
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


def test_content_script_view_serves_bootstrap_for_match_only_extensions(client) -> None:
    """Serve bootstrap detection script when only URL match patterns are configured."""
    extension = JsExtension.objects.create(
        slug="match-only",
        name="Match Only",
        matches="https://example.com/*",
        content_script="",
    )

    url = reverse("extensions:content", args=[extension.slug])
    response = client.get(url)

    assert response.status_code == 200
    response_payload = response.content.decode("utf-8")
    assert "Arthexis site detected" in response_payload


def test_missing_assets_return_404(client) -> None:
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


def test_extension_catalog_and_download_archive(client) -> None:
    """Expose extension catalog and downloadable archive for enabled extensions."""
    extension = JsExtension.objects.create(
        slug="github-resolve-open-comments-test",
        name="GitHub Resolve Open Comments",
        description="Resolve helper",
        version="1.0.0",
        manifest_version=3,
        matches="https://github.com/*",
        content_script="console.log('x');",
        options_page="<html><body>Options</body></html>",
        permissions="storage",
        host_permissions="https://github.com/*",
    )
    JsExtension.objects.create(
        slug="disabled-catalog-entry",
        name="Disabled",
        is_enabled=False,
        content_script="console.log('x');",
    )

    catalog_response = client.get(reverse("extensions:catalog"))
    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    matching_entries = [entry for entry in catalog_payload["extensions"] if entry["slug"] == extension.slug]
    assert len(matching_entries) == 1
    entry = matching_entries[0]
    assert entry["download_url"].endswith(f"/extensions/{extension.slug}/download.zip")

    download_response = client.get(
        reverse("extensions:download", args=[extension.slug])
    )
    assert download_response.status_code == 200
    assert (
        'attachment; filename="github-resolve-open-comments-test-1.0.0.zip"'
        in download_response["Content-Disposition"]
    )

    with zipfile.ZipFile(io.BytesIO(download_response.content)) as archive:
        names = sorted(archive.namelist())
        assert names == ["content.js", "manifest.json", "options.html"]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["name"] == "GitHub Resolve Open Comments"


def test_seeded_slug_archive_includes_options_script(client) -> None:
    """Ensure seeded GitHub extension slug includes special options.js archive file."""
    extension = JsExtension.objects.create(
        slug="github-resolve-open-comments",
        name="GitHub Resolve Open Comments",
        description="Resolve helper",
        version="1.0.0",
        manifest_version=3,
        matches="https://github.com/*",
        content_script="console.log('x');",
        options_page="<html><body>Options</body></html>",
        permissions="storage",
        host_permissions="https://github.com/*",
    )

    download_response = client.get(reverse("extensions:download", args=[extension.slug]))
    assert download_response.status_code == 200

    with zipfile.ZipFile(io.BytesIO(download_response.content)) as archive:
        names = sorted(archive.namelist())
        assert names == ["content.js", "manifest.json", "options.html", "options.js"]


def test_github_seeded_extension_templates_expose_bulk_actions() -> None:
    """Include the expected UI strings in the seeded GitHub helper templates."""
    payload = JsExtension.github_resolve_comments_extension_defaults()

    assert payload["slug"] == "github-resolve-open-comments"
    assert "Resolve all open comments" in str(payload["content_script"])
    assert "Resolve all with comment" in str(payload["content_script"])
    assert "Default comment text" in str(payload["options_page"])
    assert '<script src="options.js"></script>' in str(payload["options_page"])
    assert "loadSettings" in JsExtension.github_resolve_comments_options_script()
