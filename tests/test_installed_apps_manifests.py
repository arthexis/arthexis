"""Regression tests for conventional INSTALLED_APPS loading."""

from __future__ import annotations

from django.apps import AppConfig, apps as django_apps

from config.settings import apps as app_settings


def test_local_apps_use_standard_package_discovery() -> None:
    """Regression: local apps are discovered from ``apps/*`` package structure."""

    discovered = app_settings._load_local_apps()

    assert discovered
    assert discovered == sorted(discovered)
    assert "apps.base" in discovered
    assert "apps.core" in discovered
    assert "apps.ocpp.forwarder" in discovered


def test_local_apps_are_importable_through_appconfig() -> None:
    """Regression: each discovered app entry resolves through ``AppConfig.create``."""

    for app_entry in app_settings._load_local_apps():
        config = AppConfig.create(app_entry)

        assert config.name == app_entry


def test_installed_apps_include_core_in_registry() -> None:
    """Regression: ``apps.core`` remains available via Django's app registry."""

    core_config = django_apps.get_app_config("core")

    assert core_config.name == "apps.core"
    assert "apps.core" in app_settings.INSTALLED_APPS
