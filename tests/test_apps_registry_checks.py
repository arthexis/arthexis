"""Tests for app registry system checks."""

from django.core.checks import run_checks

from apps.core.checks.apps_registry import (
    APPS_REGISTRY_ENTRY_NOT_IMPORTABLE_ID,
    APPS_REGISTRY_UNLISTED_LOCAL_APP_ID,
)


def test_apps_registry_check_reports_import_and_listing_errors(settings):
    settings.PROJECT_LOCAL_APPS = ["apps.core", "apps.this_app_does_not_exist"]
    settings.PROJECT_APPS = ["config.auth_app.AuthConfig"]
    settings.INSTALLED_APPS = ["apps.core", "apps.audio"]

    errors = run_checks(tags=["core"])

    assert any(
        error.id == APPS_REGISTRY_ENTRY_NOT_IMPORTABLE_ID
        and "apps.this_app_does_not_exist" in error.msg
        for error in errors
    )
    assert any(
        error.id == APPS_REGISTRY_UNLISTED_LOCAL_APP_ID
        and "apps.audio" in error.msg
        for error in errors
    )
