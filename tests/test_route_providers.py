"""Tests for route-provider discovery and compatibility behavior."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
import sys

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

import config.route_providers as route_providers


@override_settings(ROUTE_PROVIDER_ENABLE_LEGACY_FALLBACK=False)
def test_explicit_route_provider_discovery_without_legacy_fallback():
    """Route discovery should continue to resolve explicit providers when fallback is disabled."""

    routes = {str(pattern.pattern) for pattern in route_providers.autodiscovered_route_patterns()}

    assert "blog/" not in routes
    assert "awg/" in routes
    assert "actions/api/" in routes


def test_legacy_fallback_warns_when_app_relies_on_implicit_urls(monkeypatch):
    """Legacy implicit ``urls`` mounting should warn while compatibility mode is enabled."""

    module_name = "legacy_fake_app"
    urls_name = f"{module_name}.urls"

    fake_module = ModuleType(module_name)
    fake_urls = ModuleType(urls_name)
    fake_urls.urlpatterns = []

    monkeypatch.setitem(sys.modules, module_name, fake_module)
    monkeypatch.setitem(sys.modules, urls_name, fake_urls)
    monkeypatch.setattr(
        route_providers,
        "_iter_project_apps",
        lambda: [
            SimpleNamespace(
                name=module_name,
                label="legacy-fake",
                path="/workspace/arthexis/apps/legacy_fake_app",
            )
        ],
    )

    with pytest.deprecated_call(match="Implicit route-provider fallback include is deprecated"):
        patterns = route_providers.autodiscovered_route_patterns()

    assert {str(pattern.pattern) for pattern in patterns} == {"legacy-fake/"}


@override_settings(ROUTE_PROVIDER_ENABLE_LEGACY_FALLBACK=False)
def test_legacy_fallback_disabled_fails_for_apps_without_routes(monkeypatch):
    """Disabling fallback should fail fast for apps that lack ``routes.py``."""

    module_name = "legacy_fake_app_disabled"
    urls_name = f"{module_name}.urls"

    fake_module = ModuleType(module_name)
    fake_urls = ModuleType(urls_name)
    fake_urls.urlpatterns = []

    monkeypatch.setitem(sys.modules, module_name, fake_module)
    monkeypatch.setitem(sys.modules, urls_name, fake_urls)
    monkeypatch.setattr(
        route_providers,
        "_iter_project_apps",
        lambda: [
            SimpleNamespace(
                name=module_name,
                label="legacy-fake-disabled",
                path="/workspace/arthexis/apps/legacy_fake_app_disabled",
            )
        ],
    )

    with pytest.raises(ImproperlyConfigured, match="fallback include is disabled"):
        route_providers.autodiscovered_route_patterns()


def test_duplicate_prefix_detection_fails_for_overlapping_cross_app_mounts():
    """Overlapping roots from different apps should raise a deterministic configuration error."""

    with pytest.raises(ImproperlyConfigured, match="conflicting route-provider root mounts"):
        route_providers._detect_conflicting_roots(
            [
                ("shared/", "app_a", "apps.app_a.routes"),
                ("shared/v2/", "app_b", "apps.app_b.routes"),
            ]
        )
