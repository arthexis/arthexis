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
from django.core.exceptions import ImproperlyConfigured
from django.urls.resolvers import URLPattern, URLResolver


def _iter_project_apps() -> Iterable:
    """Yield app configs that are part of this repository's ``apps`` package.

    This intentionally ignores third-party packages that may also live under
    ``BASE_DIR`` (for example when a local virtualenv lives at ``.venv/`` in
    the repository root).
    """

    base_dir = Path(settings.BASE_DIR).resolve()
    apps_dir = Path(getattr(settings, "APPS_DIR", base_dir / "apps")).resolve()
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path).resolve()
        try:
            app_path.relative_to(apps_dir)
        except ValueError:
            continue
        yield app_config


def _module_exists(module_name: str) -> bool:
    """Return whether importing ``module_name`` succeeds."""

    try:
        import_module(module_name)
    except ModuleNotFoundError:
        return False
    return True


def _normalize_literal_route(route: str) -> str:
    """Normalize a literal path route for overlap checks."""

    if not route:
        return ""
    return route.strip("/") + "/"


def _is_overlap(prefix: str, other_prefix: str) -> bool:
    """Return whether two mounted literal prefixes overlap by segment boundary."""

    if not prefix or not other_prefix:
        return False
    return prefix.startswith(other_prefix) or other_prefix.startswith(prefix)


def _detect_conflicting_roots(mounts: list[tuple[str, str, str]]) -> None:
    """Fail fast when two distinct apps claim overlapping mounted prefixes."""

    normalized = [(_normalize_literal_route(p), owner, source) for p, owner, source in mounts]
    conflicts: set[tuple[str, str, str, str]] = set()
    for index, (prefix, owner, source) in enumerate(normalized):
        for other_prefix, other_owner, other_source in normalized[index + 1 :]:
            if owner == other_owner:
                continue
            if not _is_overlap(prefix, other_prefix):
                continue

            left = (prefix, owner, source)
            right = (other_prefix, other_owner, other_source)
            if left > right:
                left, right = right, left
            conflicts.add((left[0], left[1], right[0], right[1]))

    if conflicts:
        conflict_list = ", ".join(
            f"'{left}' ({left_owner}) vs '{right}' ({right_owner})"
            for left, left_owner, right, right_owner in sorted(conflicts)
        )
        raise ImproperlyConfigured(
            f"Detected conflicting route-provider root mounts: {conflict_list}."
        )


def _warn_or_fail_for_legacy_fallback(legacy_apps: list[str]) -> None:
    """Fail when applications rely on implicit legacy URL include fallback."""

    if not legacy_apps:
        return

    app_list = ", ".join(sorted(legacy_apps))
    raise ImproperlyConfigured(
        "Implicit route-provider fallback include has been removed. "
        f"Add routes.py with ROOT_URLPATTERNS for: {app_list}."
    )


def autodiscovered_route_patterns() -> list[URLPattern | URLResolver]:
    """Collect root route providers from project apps.

    Required convention:
    - ``apps/<app>/routes.py`` exporting ``ROOT_URLPATTERNS``.

    Legacy ``urls`` and ``api.urls`` fallback auto-includes are no longer
    supported.
    """

    patterns: list[URLPattern | URLResolver] = []
    mounted_prefixes: list[tuple[str, str, str]] = []
    apps_relying_on_legacy_fallback: list[str] = []

    for app_config in _iter_project_apps():
        routes_module_name = f"{app_config.name}.routes"
        app_urls_module = f"{app_config.name}.urls"
        app_api_urls_module = f"{app_config.name}.api.urls"

        try:
            routes_module = import_module(routes_module_name)
        except ModuleNotFoundError:
            routes_module = None

        has_legacy_urls = _module_exists(app_urls_module)
        has_legacy_api_urls = _module_exists(app_api_urls_module)

        if routes_module is None:
            if has_legacy_urls or has_legacy_api_urls:
                apps_relying_on_legacy_fallback.append(app_config.label)
            continue

        root_patterns = getattr(routes_module, "ROOT_URLPATTERNS", None)
        if root_patterns is None:
            raise AttributeError(f"{routes_module_name} must define ROOT_URLPATTERNS")

        patterns.extend(root_patterns)
        for root_pattern in root_patterns:
            mounted_prefixes.append(
                (str(root_pattern.pattern), app_config.label, routes_module_name)
            )

    _warn_or_fail_for_legacy_fallback(apps_relying_on_legacy_fallback)
    _detect_conflicting_roots(mounted_prefixes)

    return patterns
