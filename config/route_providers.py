"""Utilities for collecting project route providers.

The route-provider convention allows each app to expose top-level URL patterns
from ``apps/<app>/routes.py`` as ``ROOT_URLPATTERNS``.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Iterable

from django.apps import apps
from django.conf import settings
from django.urls import include, path
from django.urls.resolvers import URLPattern, URLResolver


def _iter_project_apps() -> Iterable:
    """Yield app configs that live within the project source tree."""

    base_dir = Path(settings.BASE_DIR).resolve()
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path).resolve()
        try:
            app_path.relative_to(base_dir)
        except ValueError:
            continue
        yield app_config


def _include_if_exists(app_config, module_suffix: str, prefix: str):
    """Return a mounted URL include pattern when an optional module exists."""

    module_name = f"{app_config.name}.{module_suffix}"
    try:
        import_module(module_name)
    except ModuleNotFoundError:
        return None
    return path(prefix, include(module_name))


def _patterns_include_module(
    patterns: Iterable[URLPattern | URLResolver], module_name: str
) -> bool:
    """Return whether ``patterns`` already include ``module_name``.

    ``django.urls.include`` may store the imported URLConf in ``urlconf_name``
    either as:

    * a dotted module path string, or
    * a tuple of ``(module, app_name, namespace)``.

    We normalize both forms so route-provider fallback includes do not mount the
    same app URLConf more than once.
    """

    for pattern in patterns:
        urlconf = getattr(pattern, "urlconf_name", None)
        if urlconf == module_name:
            return True

        if hasattr(urlconf, "__name__") and urlconf.__name__ == module_name:
            return True

        if isinstance(urlconf, tuple) and urlconf:
            included = urlconf[0]
            if included == module_name:
                return True

            if hasattr(included, "__name__") and included.__name__ == module_name:
                return True
    return False


def autodiscovered_route_patterns() -> list[URLPattern]:
    """Collect root route providers from project apps.

    Preferred convention:
    - ``apps/<app>/routes.py`` exporting ``ROOT_URLPATTERNS``.

    Compatibility fallback:
    - Include legacy ``urls`` under ``/<app_label>/`` when ``routes.py`` is
      absent, or when ``routes.py`` does not already mount that app's
      ``urls`` module.
    - Include optional ``api.urls`` under ``/<app_label>/api/`` when present.
    """

    patterns: list[URLPattern] = []
    for app_config in _iter_project_apps():
        routes_module_name = f"{app_config.name}.routes"
        try:
            routes_module = import_module(routes_module_name)
        except ModuleNotFoundError:
            routes_module = None

        has_routes_module = routes_module is not None
        root_patterns: list[URLPattern | URLResolver] = []
        if has_routes_module:
            root_patterns = getattr(routes_module, "ROOT_URLPATTERNS", None)
            if root_patterns is None:
                raise AttributeError(
                    f"{routes_module_name} must define ROOT_URLPATTERNS"
                )
            patterns.extend(root_patterns)

        app_urls_module = f"{app_config.name}.urls"
        routes_already_include_app_urls = _patterns_include_module(
            root_patterns, app_urls_module
        )
        if not routes_already_include_app_urls:
            urls_pattern = _include_if_exists(
                app_config,
                "urls",
                f"{app_config.label}/",
            )
            if urls_pattern:
                patterns.append(urls_pattern)

        api_pattern = _include_if_exists(app_config, "api.urls", f"{app_config.label}/api/")
        if api_pattern:
            patterns.append(api_pattern)

    return patterns
