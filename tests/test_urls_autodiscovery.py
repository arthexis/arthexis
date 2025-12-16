from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
import sys

from django.apps import AppConfig, apps
from django.conf import settings

from config.urls import autodiscovered_urlpatterns


def _pattern_routes():
    return {pattern.pattern._route for pattern in autodiscovered_urlpatterns()}


def _project_app_admin_modules():
    base_dir = Path(settings.BASE_DIR).resolve()
    modules = []
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path).resolve()
        try:
            app_path.relative_to(base_dir)
        except ValueError:
            continue

        module_name = f"{app_config.name}.admin"
        if importlib.util.find_spec(module_name):
            modules.append(module_name)

    return modules


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


def test_api_routes_are_only_namespaced_by_app():
    routes = _pattern_routes()

    assert all(not route.startswith("api/") for route in routes)

    base_dir = Path(settings.BASE_DIR).resolve()
    app_api_prefixes = set()
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path).resolve()
        try:
            app_path.relative_to(base_dir)
        except ValueError:
            continue

        if app_config.label in {"pages", "docs"}:
            continue

        app_api_prefixes.add(f"{app_config.label}/api/")

    api_routes = [route for route in routes if "/api/" in route]
    assert all(any(route.startswith(prefix) for prefix in app_api_prefixes) for route in api_routes)


def test_admin_modules_are_loaded_during_url_configuration(monkeypatch):
    import config.urls as urls

    admin_modules = _project_app_admin_modules()

    # Reload URL configuration with a fresh sys.modules to assert admin modules are imported.
    modules_copy = sys.modules.copy()
    monkeypatch.setattr(sys, "modules", modules_copy)

    importlib.reload(urls)

    assert all(module_name in sys.modules for module_name in admin_modules)
