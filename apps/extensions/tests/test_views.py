"""Integration tests for hosted extension catalog and download views."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.urls import reverse

from apps.extensions.models import JsExtension

pytestmark = [pytest.mark.django_db]


def test_extension_catalog_lists_enabled_extensions_only(client) -> None:
    """Catalog endpoint should expose only enabled extension entries.

    Parameters:
        client: Django test client fixture.

    Returns:
        None
    """

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

    response = client.get(reverse("extensions:catalog"))

    assert response.status_code == 200
    payload = response.json()
    matching_entries = [
        entry for entry in payload["extensions"] if entry["slug"] == extension.slug
    ]
    assert len(matching_entries) == 1
    assert matching_entries[0]["download_url"].endswith(
        f"/extensions/{extension.slug}/download.zip"
    )


def test_extension_download_archive_contains_manifest_and_assets(client) -> None:
    """Download endpoint should return an archive with manifest and extension assets.

    Parameters:
        client: Django test client fixture.

    Returns:
        None
    """

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

    response = client.get(reverse("extensions:download", args=[extension.slug]))

    assert response.status_code == 200
    assert (
        'attachment; filename="github-resolve-open-comments-test-1.0.0.zip"'
        in response["Content-Disposition"]
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = sorted(archive.namelist())
        assert names == ["content.js", "manifest.json", "options.html"]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["name"] == "GitHub Resolve Open Comments"
