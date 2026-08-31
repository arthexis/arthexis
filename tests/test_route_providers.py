from __future__ import annotations

import sys
from types import ModuleType

from django.http import HttpResponse
from django.urls import path

from config import route_providers


def _ok_view(request):
    return HttpResponse("ok")


def test_route_provider_app_resolution_uses_installed_apps_before_apps_ready(
    monkeypatch, settings
):
    settings.INSTALLED_APPS = ["apps.core", "django.contrib.contenttypes"]
    monkeypatch.setattr(route_providers.django_apps, "apps_ready", False)

    assert route_providers._route_provider_app_is_installed("apps.core.routes")
    assert not route_providers._route_provider_app_is_installed("apps.disabled.routes")
    assert route_providers._route_provider_app_is_installed("config.custom_routes")


def test_autodiscovery_skips_disabled_app_route_modules(monkeypatch, settings):
    enabled_module_name = "config.test_enabled_routes"
    enabled_module = ModuleType(enabled_module_name)
    enabled_module.ROOT_URLPATTERNS = [
        path("enabled/", _ok_view, name="test-route-provider-enabled")
    ]

    monkeypatch.setitem(sys.modules, enabled_module_name, enabled_module)
    settings.INSTALLED_APPS = ["apps.core", "django.contrib.contenttypes"]
    settings.ROUTE_PROVIDERS = [
        "apps.disabled.routes",
        enabled_module_name,
    ]
    monkeypatch.setattr(route_providers.django_apps, "apps_ready", False)

    patterns = route_providers.autodiscovered_route_patterns()

    assert [str(pattern.pattern) for pattern in patterns] == ["enabled/"]


def test_asgi_autodiscovery_skips_disabled_websocket_route_modules(
    monkeypatch, settings
):
    enabled_module_name = "apps.core.routing"
    enabled_module = ModuleType(enabled_module_name)
    enabled_module.websocket_urlpatterns = [
        path("ws/enabled/", _ok_view, name="test-asgi-enabled")
    ]
    fallback_module_name = "config.test_fallback_websocket_routes"
    fallback_module = ModuleType(fallback_module_name)
    fallback_module.websocket_urlpatterns = [
        path("ws/fallback/", _ok_view, name="test-asgi-fallback")
    ]

    monkeypatch.setitem(sys.modules, enabled_module_name, enabled_module)
    monkeypatch.setitem(sys.modules, fallback_module_name, fallback_module)
    settings.INSTALLED_APPS = ["apps.core", "django.contrib.contenttypes"]
    settings.ASGI_ROUTE_PROVIDERS = [
        "apps.disabled.routing",
        enabled_module_name,
        fallback_module_name,
    ]
    monkeypatch.setattr(route_providers.django_apps, "apps_ready", False)

    patterns = route_providers.autodiscovered_websocket_urlpatterns()

    assert [str(pattern.pattern) for pattern in patterns] == [
        "ws/enabled/",
        "ws/fallback/",
    ]


def test_route_provider_app_resolution_rejects_disabled_route_apps(
    monkeypatch, settings
):
    settings.INSTALLED_APPS = [
        "apps.ocpp",
        "django.contrib.contenttypes",
    ]
    settings.ROUTE_PROVIDER_DISABLED_APPS = ["apps.ocpp"]
    monkeypatch.setattr(route_providers.django_apps, "apps_ready", False)

    assert not route_providers._route_provider_app_is_installed("apps.ocpp.routes")
    assert route_providers._route_provider_app_is_installed("config.custom_routes")
