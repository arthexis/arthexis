from __future__ import annotations

from pathlib import Path
from types import ModuleType
import sys

from django.apps import AppConfig, apps
from django.conf import settings
from django.urls import path

from config.urls import autodiscovered_urlpatterns


def _pattern_routes():
    routes = set()

    for pattern in autodiscovered_urlpatterns():
        base_route = pattern.pattern._route
        routes.add(base_route)

        if getattr(pattern, "url_patterns", None):
            for nested_pattern in pattern.url_patterns:
                routes.add(f"{base_route}{nested_pattern.pattern._route}")

    return routes


def test_autodiscovery_includes_known_apps_with_app_namespaces():
    routes = _pattern_routes()

    assert "core/" in routes
    assert "cards/" in routes
    assert "tasks/" in routes  # standard prefix
    assert "api/rfid/" not in routes
    assert "rfid/" not in routes


def test_pages_and_docs_are_excluded_from_autodiscovery():
    routes = _pattern_routes()

    assert "pages/" not in routes
    assert "docs/" not in routes


def test_third_party_apps_outside_base_dir_are_skipped(monkeypatch):
    class ExternalConfig(AppConfig):
        name = "external_app"
        label = "external"
        path = str(Path(settings.BASE_DIR).parent / "external_app")

    external_module = ModuleType("external_app")
    external_module.__file__ = str(Path(ExternalConfig.path) / "__init__.py")
    external_module.__path__ = [ExternalConfig.path]

    external_config = ExternalConfig("external_app", external_module)
    real_configs = list(apps.get_app_configs())
    monkeypatch.setattr(apps, "get_app_configs", lambda: [external_config, *real_configs])

    routes = _pattern_routes()

    assert "external/" not in routes
    assert "core/" in routes


def test_api_modules_are_namespaced_under_their_app(monkeypatch):
    app_config = apps.get_app_config("core")

    api_pkg_name = f"{app_config.name}.api"
    api_urls_name = f"{api_pkg_name}.urls"

    api_package = ModuleType(api_pkg_name)
    api_package.__path__ = []
    api_urls_module = ModuleType(api_urls_name)
    api_urls_module.urlpatterns = []

    monkeypatch.setitem(sys.modules, api_pkg_name, api_package)
    monkeypatch.setitem(sys.modules, api_urls_name, api_urls_module)

    routes = _pattern_routes()

    assert f"{app_config.label}/api/" in routes


def test_apps_without_urls_do_not_raise(monkeypatch):
    app_without_urls = apps.get_app_config("aws")
    monkeypatch.setattr(apps, "get_app_configs", lambda: [app_without_urls])

    routes = _pattern_routes()

    assert routes == set()


def test_autodiscovery_prefixes_api_routes_with_app_label(monkeypatch):
    class AlphaConfig(AppConfig):
        name = "alpha"
        label = "alpha"
        path = str(Path(settings.BASE_DIR) / "alpha")

    alpha_module = ModuleType("alpha")
    alpha_module.__file__ = str(Path(AlphaConfig.path) / "__init__.py")
    alpha_module.__path__ = [AlphaConfig.path]

    api_package = ModuleType("alpha.api")
    api_package.__path__ = []
    api_urls = ModuleType("alpha.api.urls")
    api_urls.urlpatterns = [path("beta/", lambda request: None)]

    alpha_config = AlphaConfig("alpha", alpha_module)
    real_configs = list(apps.get_app_configs())
    monkeypatch.setattr(apps, "get_app_configs", lambda: [alpha_config, *real_configs])

    monkeypatch.setitem(sys.modules, "alpha", alpha_module)
    monkeypatch.setitem(sys.modules, "alpha.api", api_package)
    monkeypatch.setitem(sys.modules, "alpha.api.urls", api_urls)

    routes = _pattern_routes()

    assert "alpha/api/beta/" in routes
    assert "beta/api/" not in routes
