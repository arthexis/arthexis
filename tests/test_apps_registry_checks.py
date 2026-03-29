"""Tests for app registry system checks."""

from importlib import import_module

import pytest

from django.core.checks import run_checks
from django.core.exceptions import ImproperlyConfigured


def test_apps_registry_check_reports_import_and_listing_errors(settings):
    settings.PROJECT_LOCAL_APPS = ["apps.core", "apps.this_app_does_not_exist"]
    settings.PROJECT_APPS = ["config.auth_app.AuthConfig"]
    settings.INSTALLED_APPS = ["apps.core", "apps.audio"]

    errors = run_checks(tags=["core"])

    assert any(
        error.id == "core.E001"
        and "apps.this_app_does_not_exist" in error.msg
        for error in errors
    )
    assert any(
        error.id == "core.E002"
        and "apps.audio" in error.msg
        for error in errors
    )


def test_enforce_apps_registry_configuration_raises_for_misconfigured_apps(settings):
    settings.PROJECT_LOCAL_APPS = ["apps.this_app_does_not_exist"]
    settings.PROJECT_APPS = []
    settings.INSTALLED_APPS = ["apps.core", "apps.audio"]

    apps_registry = import_module("apps.core.checks.apps_registry")

    with pytest.raises(ImproperlyConfigured, match=r"core\.E001"):
        apps_registry.enforce_apps_registry_configuration()
