from __future__ import annotations

from pathlib import Path

from django.apps import AppConfig, apps
from django.conf import settings

from config.urls import autodiscovered_urlpatterns


def _pattern_routes():
    return {pattern.pattern._route for pattern in autodiscovered_urlpatterns()}


def test_autodiscovery_includes_known_apps_with_overrides():
    routes = _pattern_routes()

    assert "api/rfid/" in routes  # apps.core override
    assert "rfid/" in routes  # apps.cards override
    assert "tasks/" in routes  # standard prefix without override
    assert "core/" not in routes


def test_pages_and_docs_are_excluded_from_autodiscovery():
    routes = _pattern_routes()

    assert "pages/" not in routes
    assert "docs/" not in routes


def test_third_party_apps_outside_base_dir_are_skipped(monkeypatch):
    class ExternalConfig(AppConfig):
        name = "external_app"
        label = "external"
        path = str(Path(settings.BASE_DIR).parent / "external_app")

    external_config = ExternalConfig("external_app", ExternalConfig.path)
    real_configs = list(apps.get_app_configs())
    monkeypatch.setattr(apps, "get_app_configs", lambda: [external_config, *real_configs])

    routes = _pattern_routes()

    assert "external/" not in routes
    assert "api/rfid/" in routes


def test_apps_without_urls_do_not_raise(monkeypatch):
    app_without_urls = apps.get_app_config("aws")
    monkeypatch.setattr(apps, "get_app_configs", lambda: [app_without_urls])

    routes = _pattern_routes()

    assert routes == set()
