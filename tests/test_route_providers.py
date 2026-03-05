"""Tests for route-provider discovery and compatibility behavior."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

import config.route_providers as route_providers


@override_settings(ROUTE_PROVIDER_ENABLE_LEGACY_FALLBACK=False)
def test_explicit_route_provider_discovery_without_legacy_fallback():
    """Fallback-disable mode should fail when any app still relies on implicit URL mounting."""

    with pytest.raises(ImproperlyConfigured, match="fallback include is disabled"):
        route_providers.autodiscovered_route_patterns()


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


def test_legacy_fallback_warns_when_routes_module_still_relies_on_implicit_urls(monkeypatch):
    """Apps with ``routes.py`` should still be flagged if they rely on implicit fallback includes."""

    module_name = "legacy_fake_app_routes"
    routes_name = f"{module_name}.routes"
    urls_name = f"{module_name}.urls"

    fake_module = ModuleType(module_name)
    fake_routes = ModuleType(routes_name)
    fake_routes.ROOT_URLPATTERNS = []
    fake_urls = ModuleType(urls_name)
    fake_urls.urlpatterns = []

    monkeypatch.setitem(sys.modules, module_name, fake_module)
    monkeypatch.setitem(sys.modules, routes_name, fake_routes)
    monkeypatch.setitem(sys.modules, urls_name, fake_urls)
    monkeypatch.setattr(
        route_providers,
        "_iter_project_apps",
        lambda: [
            SimpleNamespace(
                name=module_name,
                label="legacy-fake-routes",
                path="/workspace/arthexis/apps/legacy_fake_app_routes",
            )
        ],
    )

    with pytest.deprecated_call(match="Implicit route-provider fallback include is deprecated"):
        patterns = route_providers.autodiscovered_route_patterns()

    assert {str(pattern.pattern) for pattern in patterns} == {"legacy-fake-routes/"}


@override_settings(ROUTE_PROVIDER_ENABLE_LEGACY_FALLBACK=False)
def test_legacy_fallback_disabled_fails_when_routes_module_omits_legacy_mounts(monkeypatch):
    """Disabling fallback should fail for apps with ``routes.py`` that still rely on implicit includes."""

    module_name = "legacy_fake_app_routes_disabled"
    routes_name = f"{module_name}.routes"
    api_urls_name = f"{module_name}.api.urls"

    fake_module = ModuleType(module_name)
    fake_routes = ModuleType(routes_name)
    fake_routes.ROOT_URLPATTERNS = []
    fake_api_urls = ModuleType(api_urls_name)
    fake_api_urls.urlpatterns = []

    monkeypatch.setitem(sys.modules, module_name, fake_module)
    monkeypatch.setitem(sys.modules, routes_name, fake_routes)
    monkeypatch.setitem(sys.modules, api_urls_name, fake_api_urls)
    monkeypatch.setattr(
        route_providers,
        "_iter_project_apps",
        lambda: [
            SimpleNamespace(
                name=module_name,
                label="legacy-fake-routes-disabled",
                path="/workspace/arthexis/apps/legacy_fake_app_routes_disabled",
            )
        ],
    )

    with pytest.raises(ImproperlyConfigured, match="fallback include is disabled"):
        route_providers.autodiscovered_route_patterns()


def test_legacy_api_urls_are_not_double_included_when_explicitly_mounted(monkeypatch):
    """Fallback should skip ``api.urls`` when it is already included by ``ROOT_URLPATTERNS``."""

    module_name = "legacy_fake_app_api"
    routes_name = f"{module_name}.routes"
    api_urls_name = f"{module_name}.api.urls"

    fake_module = ModuleType(module_name)
    fake_routes = ModuleType(routes_name)
    fake_api_urls = ModuleType(api_urls_name)
    fake_api_urls.urlpatterns = []

    monkeypatch.setitem(sys.modules, module_name, fake_module)
    monkeypatch.setitem(sys.modules, routes_name, fake_routes)
    monkeypatch.setitem(sys.modules, api_urls_name, fake_api_urls)

    fake_routes.ROOT_URLPATTERNS = [
        route_providers.path("legacy-fake-api/api/", route_providers.include(api_urls_name))
    ]
    monkeypatch.setattr(
        route_providers,
        "_iter_project_apps",
        lambda: [
            SimpleNamespace(
                name=module_name,
                label="legacy-fake-api",
                path="/workspace/arthexis/apps/legacy_fake_app_api",
            )
        ],
    )

    patterns = route_providers.autodiscovered_route_patterns()

    assert [str(pattern.pattern) for pattern in patterns] == ["legacy-fake-api/api/"]


def test_duplicate_prefix_detection_fails_for_overlapping_cross_app_mounts():
    """Overlapping roots from different apps should raise a deterministic configuration error."""

    with pytest.raises(ImproperlyConfigured, match="conflicting route-provider root mounts"):
        route_providers._detect_conflicting_roots(
            [
                ("shared/", "app_a", "apps.app_a.routes"),
                ("shared/v2/", "app_b", "apps.app_b.routes"),
            ]
        )


def test_iter_project_apps_ignores_base_dir_packages_outside_apps_dir(monkeypatch, settings):
    """App discovery should ignore modules under ``BASE_DIR`` but outside ``APPS_DIR``."""

    settings.BASE_DIR = "/workspace/arthexis"
    settings.APPS_DIR = "/workspace/arthexis/apps"

    in_repo_app = SimpleNamespace(path="/workspace/arthexis/apps/example")
    virtualenv_app = SimpleNamespace(path="/workspace/arthexis/.venv/lib/python/site-packages/demo")

    monkeypatch.setattr(
        route_providers.apps,
        "get_app_configs",
        lambda: [in_repo_app, virtualenv_app],
    )

    discovered = list(route_providers._iter_project_apps())

    assert discovered == [in_repo_app]


def test_autodiscovery_ignores_vendored_apps_outside_apps_dir(monkeypatch):
    """Regression: third-party apps under ``.venv`` should not be treated as project route providers."""

    base_dir = Path("C:/workspace/repo")
    apps_dir = base_dir / "apps"
    first_party_path = apps_dir / "fake_core_app"
    vendored_path = base_dir / ".venv" / "Lib" / "site-packages" / "django" / "contrib" / "admindocs"

    first_party_module = "fake_core_app"
    first_party_routes = f"{first_party_module}.routes"
    vendored_module = "fake_vendor_admindocs"
    vendored_urls = f"{vendored_module}.urls"

    fake_first_party = ModuleType(first_party_module)
    fake_first_party_routes = ModuleType(first_party_routes)
    fake_first_party_routes.ROOT_URLPATTERNS = [
        route_providers.path("admindocs/commands/", lambda request: None)
    ]
    fake_vendored = ModuleType(vendored_module)
    fake_vendored_urls = ModuleType(vendored_urls)
    fake_vendored_urls.urlpatterns = []

    monkeypatch.setitem(sys.modules, first_party_module, fake_first_party)
    monkeypatch.setitem(sys.modules, first_party_routes, fake_first_party_routes)
    monkeypatch.setitem(sys.modules, vendored_module, fake_vendored)
    monkeypatch.setitem(sys.modules, vendored_urls, fake_vendored_urls)
    monkeypatch.setattr(route_providers.settings, "BASE_DIR", base_dir)
    monkeypatch.setattr(route_providers.settings, "APPS_DIR", apps_dir)
    monkeypatch.setattr(
        route_providers.apps,
        "get_app_configs",
        lambda: [
            SimpleNamespace(
                name=first_party_module,
                label="core",
                path=str(first_party_path),
            ),
            SimpleNamespace(
                name=vendored_module,
                label="admindocs",
                path=str(vendored_path),
            ),
        ],
    )

    patterns = route_providers.autodiscovered_route_patterns()
    mounted_prefixes = [str(pattern.pattern) for pattern in patterns]

    assert "admindocs/commands/" in mounted_prefixes
    assert "admindocs/" not in mounted_prefixes
