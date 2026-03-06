"""Tests for configurable admin URL path helpers and mounts."""

from __future__ import annotations

import importlib

import pytest
from django.test import override_settings

from config import admin_urls


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("admin/", "admin/"),
        ("admin", "admin/"),
        ("/control-panel/", "control-panel/"),
        ("  secure-admin  ", "secure-admin/"),
    ],
)
def test_normalize_admin_url_path_returns_trailing_slash_fragment(raw_path: str, expected: str):
    """Normalization should produce a clean route fragment accepted by ``path``."""

    assert admin_urls.normalize_admin_url_path(raw_path) == expected


@pytest.mark.parametrize(
    "raw_path",
    ["", "   ", "/", "///", "<path:any>/", "admindocs/", "i18n/", "__debug__/"],
)
def test_normalize_admin_url_path_rejects_invalid_path(raw_path: str):
    """Blank, dynamic, and reserved-prefix paths should be rejected."""

    with pytest.raises(ValueError, match="Admin URL path"):
        admin_urls.normalize_admin_url_path(raw_path)


@override_settings(ADMIN_URL_PATH="control/")
def test_admin_route_and_mount_path_respect_configured_prefix():
    """Runtime helpers should build URLs from ``settings.ADMIN_URL_PATH``."""

    assert admin_urls.admin_route() == "control/"
    assert admin_urls.admin_route("users/") == "control/users/"
    assert admin_urls.admin_mount_path() == "/control/"


@override_settings(ADMIN_URL_PATH="control/")
def test_config_urls_mounts_admin_site_at_configured_prefix():
    """Project URL configuration should mount admin using ``ADMIN_URL_PATH``."""

    urls_module = importlib.import_module("config.urls")
    urls_module = importlib.reload(urls_module)

    mounted_patterns = {str(pattern.pattern) for pattern in urls_module.urlpatterns}

    assert "control/" in mounted_patterns
    assert "admin/" not in mounted_patterns
    assert "admindocs/" in mounted_patterns


@override_settings(
    ADMIN_SITE_HEADER="Ops Header",
    ADMIN_SITE_TITLE="Ops Title",
    ADMIN_INDEX_TITLE="Ops Index",
)
def test_config_urls_applies_admin_branding_settings():
    """URL bootstrap should apply runtime admin branding values from settings."""

    urls_module = importlib.import_module("config.urls")
    urls_module = importlib.reload(urls_module)

    assert urls_module.admin.site.site_header == "Ops Header"
    assert urls_module.admin.site.site_title == "Ops Title"
    assert urls_module.admin.site.index_title == "Ops Index"


@pytest.mark.parametrize("reserved_path", ["admindocs/", "i18n/", "__debug__/"])
def test_config_urls_rejects_reserved_admin_mount_prefix(reserved_path: str):
    """Reserved route prefixes should fail during URL bootstrap."""

    with override_settings(ADMIN_URL_PATH=reserved_path):
        with pytest.raises(ValueError, match="reserved route prefix"):
            urls_module = importlib.import_module("config.urls")
            importlib.reload(urls_module)
