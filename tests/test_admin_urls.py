"""Tests for configurable admin URL path helpers and mounts."""

from __future__ import annotations

import importlib

import pytest
from django.test import override_settings

from config import admin_urls

@pytest.mark.parametrize("reserved_path", ["admindocs/", "i18n/", "__debug__/"])
def test_config_urls_rejects_reserved_admin_mount_prefix(reserved_path: str):
    """Reserved route prefixes should fail during URL bootstrap."""

    with override_settings(ADMIN_URL_PATH=reserved_path):
        with pytest.raises(ValueError, match="reserved route prefix"):
            urls_module = importlib.import_module("config.urls")
            importlib.reload(urls_module)
