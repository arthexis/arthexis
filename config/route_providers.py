"""Utilities for collecting project route providers.

The route-provider convention allows each app to expose top-level URL patterns
from ``apps/<app>/routes.py`` as ``ROOT_URLPATTERNS``.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import warnings
from typing import Iterable

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import include, path
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


def _include_if_exists(app_config, module_suffix: str, prefix: str):
    """Return a mounted URL include pattern when an optional module exists."""

    module_name = f"{app_config.name}.{module_suffix}"
    try:
        import_module(module_name)
    except ModuleNotFoundError:
        return None
    return path(prefix, include(module_name))


def _module_exists(module_name: str) -> bool:
    """Return whether importing ``module_name`` succeeds."""

    try:
        import_module(module_name)
    except ModuleNotFoundError:
        return False
    return True


def _legacy_fallback_enabled() -> bool:
    """Return whether compatibility fallback route mounting is enabled."""

    return bool(getattr(settings, "ROUTE_PROVIDER_ENABLE_LEGACY_FALLBACK", True))


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
    """Warn or fail when applications rely on implicit URL include fallbacks."""

    if not legacy_apps:
        return

    app_list = ", ".join(sorted(legacy_apps))
    if _legacy_fallback_enabled():
        warnings.warn(
            "Implicit route-provider fallback include is deprecated and will be "
            "removed in a future release. Add routes.py with ROOT_URLPATTERNS "
            f"for: {app_list}.",
            DeprecationWarning,
            stacklevel=2,
        )
        return

    raise ImproperlyConfigured(
        "Implicit route-provider fallback include is disabled by "
        "ROUTE_PROVIDER_ENABLE_LEGACY_FALLBACK=False. "
        f"Add routes.py with ROOT_URLPATTERNS for: {app_list}."
    )


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
        candidates = [urlconf]
        if isinstance(urlconf, tuple) and urlconf:
            candidates.append(urlconf[0])

        for candidate in candidates:
            if candidate == module_name:
                return True

            if hasattr(candidate, "__name__") and candidate.__name__ == module_name:
                return True
    return False


def autodiscovered_route_patterns() -> list[URLPattern]:
    """Collect root route providers from project apps.

    Preferred convention:
    - ``apps/<app>/routes.py`` exporting ``ROOT_URLPATTERNS``.

    Compatibility fallback (temporary, gated by
    ``ROUTE_PROVIDER_ENABLE_LEGACY_FALLBACK``):
    - Include legacy ``urls`` under ``/<app_label>/`` when ``routes.py`` is
      absent, or when ``routes.py`` does not already mount that app's
      ``urls`` module.
    - Include legacy ``api.urls`` under ``/<app_label>/api/`` when ``routes.py``
      is absent, or when ``routes.py`` does not already mount that app's
      ``api.urls`` module.
    """

    patterns: list[URLPattern] = []
    mounted_prefixes: list[tuple[str, str, str]] = []
    apps_relying_on_legacy_fallback: list[str] = []

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
            for root_pattern in root_patterns:
                mounted_prefixes.append(
                    (str(root_pattern.pattern), app_config.label, routes_module_name)
                )

        app_urls_module = f"{app_config.name}.urls"
        app_api_urls_module = f"{app_config.name}.api.urls"

        has_legacy_urls = _module_exists(app_urls_module)
        has_legacy_api_urls = _module_exists(app_api_urls_module)

        routes_already_include_app_urls = _patterns_include_module(
            root_patterns, app_urls_module
        )
        routes_already_include_app_api_urls = _patterns_include_module(
            root_patterns, app_api_urls_module
        )

        app_relies_on_legacy_fallback = (
            has_legacy_urls
            and not routes_already_include_app_urls
            or has_legacy_api_urls
            and not routes_already_include_app_api_urls
        )
        if app_relies_on_legacy_fallback:
            apps_relying_on_legacy_fallback.append(app_config.label)

        if _legacy_fallback_enabled() and not routes_already_include_app_urls:
            urls_pattern = _include_if_exists(
                app_config,
                "urls",
                f"{app_config.label}/",
            )
            if urls_pattern:
                patterns.append(urls_pattern)
                mounted_prefixes.append(
                    (str(urls_pattern.pattern), app_config.label, "legacy:urls")
                )

        if _legacy_fallback_enabled() and not routes_already_include_app_api_urls:
            api_pattern = _include_if_exists(
                app_config,
                "api.urls",
                f"{app_config.label}/api/",
            )
            if api_pattern:
                patterns.append(api_pattern)
                mounted_prefixes.append(
                    (str(api_pattern.pattern), app_config.label, "legacy:api.urls")
                )

    _warn_or_fail_for_legacy_fallback(apps_relying_on_legacy_fallback)
    _detect_conflicting_roots(mounted_prefixes)

    return patterns
