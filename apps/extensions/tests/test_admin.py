"""Tests for extension admin customizations."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.extensions.models import JsExtension

pytestmark = pytest.mark.django_db


def test_admin_download_archive_view_returns_zip(admin_client):
    """Download a generated extension ZIP archive from the admin change view."""
    extension = JsExtension.objects.create(
        slug="helper",
        name="Helper",
        version="1.2.3",
        matches="https://example.com/*",
        content_script="console.log('custom-script');",
        background_script="console.log('bg-script');",
        options_page="<html><body>opts</body></html>",
        permissions="storage",
    )

    response = admin_client.get(
        reverse("admin:extensions_jsextension_download", args=[extension.pk])
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert (
        f"{extension.slug}-{extension.version}.zip" in response["Content-Disposition"]
    )

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    filenames = set(archive.namelist())
    assert {"manifest.json", "content.js", "background.js", "options.html"}.issubset(
        filenames
    )

    manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    assert manifest["name"] == "Helper"
    assert manifest["content_scripts"][0]["js"] == ["content.js"]

    content_script = archive.read("content.js").decode("utf-8")
    assert "Arthexis site detected" in content_script
    assert "custom-script" in content_script


def test_admin_download_archive_view_requires_model_permission():
    """Deny archive downloads for staff users without extension permissions."""
    extension = JsExtension.objects.create(
        slug="helper-permissions",
        name="Helper",
        version="1.0.0",
        matches="https://example.com/*",
    )

    user = get_user_model().objects.create_user(
        username="staff-no-extension-access",
        password="secret",
        is_staff=True,
    )
    client = Client()
    client.force_login(user)

    response = client.get(
        reverse("admin:extensions_jsextension_download", args=[extension.pk])
    )

    assert response.status_code == 403


def test_admin_download_archive_view_sanitizes_archive_filename(admin_client):
    """Escape unsafe filename characters in archive downloads."""
    extension = JsExtension.objects.create(
        slug="helper",
        name="Helper",
        version='1.2.3"\r\n..\\unsafe',
        matches="https://example.com/*",
    )

    response = admin_client.get(
        reverse("admin:extensions_jsextension_download", args=[extension.pk])
    )

    assert response.status_code == 200
    assert response["Content-Disposition"] == (
        'attachment; filename="helper-1.2.3_.._unsafe.zip"'
    )
