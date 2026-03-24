"""Tests for route-provider autodiscovery behavior."""

import sys
from types import ModuleType, SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.urls import include, path

from config import route_providers


@pytest.fixture
def app_config(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = tmp_path / "apps"
    return SimpleNamespace(
        name="apps.example",
        label="example",
        path=str(settings.APPS_DIR / "example"),
    )


def test_autodiscovered_route_patterns_raises_for_implicit_legacy_fallback(
    monkeypatch, app_config
):
    monkeypatch.setattr(route_providers, "_iter_project_apps", lambda: [app_config])

    def fake_import_module(module_name: str):
        if module_name == "apps.example.urls":
            return ModuleType(module_name)
        raise ModuleNotFoundError(module_name)

    monkeypatch.setattr(route_providers, "import_module", fake_import_module)

    with pytest.raises(ImproperlyConfigured, match="Add routes.py with ROOT_URLPATTERNS"):
        route_providers.autodiscovered_route_patterns()


def test_autodiscovered_route_patterns_only_honor_routes_py(
    monkeypatch, app_config
):
    monkeypatch.setattr(route_providers, "_iter_project_apps", lambda: [app_config])

    routes_module = ModuleType("apps.example.routes")
    routes_module.ROOT_URLPATTERNS = [
        path("", lambda request: HttpResponse("ok"), name="example-home")
    ]

    def fake_import_module(module_name: str):
        if module_name == "apps.example.routes":
            return routes_module
        raise ModuleNotFoundError(module_name)

    monkeypatch.setattr(route_providers, "import_module", fake_import_module)

    patterns = route_providers.autodiscovered_route_patterns()

    assert len(patterns) == 1
    assert patterns[0].name == "example-home"


def test_autodiscovered_route_patterns_raises_when_legacy_urls_not_mounted(
    monkeypatch, app_config
):
    monkeypatch.setattr(route_providers, "_iter_project_apps", lambda: [app_config])

    routes_module = ModuleType("apps.example.routes")
    routes_module.ROOT_URLPATTERNS = [
        path("", lambda request: HttpResponse("ok"), name="example-home")
    ]

    def fake_import_module(module_name: str):
        if module_name == "apps.example.routes":
            return routes_module
        if module_name in {"apps.example.urls", "apps.example.api.urls"}:
            return ModuleType(module_name)
        raise ModuleNotFoundError(module_name)

    monkeypatch.setattr(route_providers, "import_module", fake_import_module)

    with pytest.raises(ImproperlyConfigured, match="Add routes.py with ROOT_URLPATTERNS"):
        route_providers.autodiscovered_route_patterns()


def test_autodiscovered_route_patterns_allow_explicit_legacy_mounts(
    monkeypatch, app_config
):
    monkeypatch.setattr(route_providers, "_iter_project_apps", lambda: [app_config])

    legacy_urls_module = ModuleType("apps.example.urls")
    legacy_urls_module.urlpatterns = []
    legacy_api_urls_module = ModuleType("apps.example.api.urls")
    legacy_api_urls_module.urlpatterns = []
    monkeypatch.setitem(sys.modules, "apps.example.urls", legacy_urls_module)
    monkeypatch.setitem(sys.modules, "apps.example.api.urls", legacy_api_urls_module)

    routes_module = ModuleType("apps.example.routes")
    routes_module.ROOT_URLPATTERNS = [
        path("", include("apps.example.urls")),
        path("api/", include("apps.example.api.urls")),
    ]

    def fake_import_module(module_name: str):
        if module_name == "apps.example.routes":
            return routes_module
        if module_name == "apps.example.urls":
            return legacy_urls_module
        if module_name == "apps.example.api.urls":
            return legacy_api_urls_module
        raise ModuleNotFoundError(module_name)

    monkeypatch.setattr(route_providers, "import_module", fake_import_module)

    patterns = route_providers.autodiscovered_route_patterns()

    assert len(patterns) == 2
