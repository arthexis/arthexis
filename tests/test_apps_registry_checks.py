"""Tests for app registry system checks."""

from importlib import import_module
from pathlib import Path

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


def _build_external_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_label: str,
    compatibility: str | None,
) -> str:
    package_dir = tmp_path / app_label
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    compatibility_line = (
        f'    arthexis_compatibility = "{compatibility}"\n'
        if compatibility is not None
        else ""
    )
    (package_dir / "apps.py").write_text(
        (
            "from django.apps import AppConfig\n\n\n"
            f"class {app_label.title()}Config(AppConfig):\n"
            f'    name = "{app_label}"\n'
            f"{compatibility_line}"
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return f"{app_label}.apps.{app_label.title()}Config"


def test_external_app_requires_declared_compatibility(settings, monkeypatch, tmp_path):
    app_config_path = _build_external_app(
        tmp_path,
        monkeypatch,
        app_label="missingcompat",
        compatibility=None,
    )
    settings.ARTHEXIS_EXTERNAL_APPS = [app_config_path]
    settings.PROJECT_LOCAL_APPS = ["apps.core"]
    settings.PROJECT_APPS = []
    settings.INSTALLED_APPS = ["apps.core", app_config_path]

    errors = run_checks(tags=["core"])

    assert any(error.id == "core.E004" and app_config_path in error.msg for error in errors)


def test_external_app_support_range_must_include_running_version(
    settings,
    monkeypatch,
    tmp_path,
):
    app_config_path = _build_external_app(
        tmp_path,
        monkeypatch,
        app_label="unsupportedcompat",
        compatibility="<0.1",
    )
    settings.ARTHEXIS_EXTERNAL_APPS = [app_config_path]
    settings.PROJECT_LOCAL_APPS = ["apps.core"]
    settings.PROJECT_APPS = []
    settings.INSTALLED_APPS = ["apps.core", app_config_path]

    errors = run_checks(tags=["core"])

    assert any(error.id == "core.E006" and app_config_path in error.msg for error in errors)
