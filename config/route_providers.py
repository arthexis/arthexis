"""Utilities for collecting project route providers."""

from __future__ import annotations

from importlib import import_module

from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls.resolvers import URLPattern, URLResolver


def _configured_route_provider_modules(
    setting_name: str = "ROUTE_PROVIDERS",
) -> list[str]:
    """Return explicitly registered route-provider module paths."""

    providers = getattr(settings, setting_name, None)
    if not isinstance(providers, (list, tuple)) or not providers:
        raise ImproperlyConfigured(
            f"{setting_name} must be a non-empty list or tuple of module paths."
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for provider in providers:
        provider_path = provider.strip() if isinstance(provider, str) else ""
        if not provider_path or provider_path.startswith("."):
            raise ImproperlyConfigured(
                f"{setting_name} entries must be non-empty absolute dotted module path strings."
            )
        if provider_path in seen:
            raise ImproperlyConfigured(
                f"{setting_name} contains a duplicate provider: {provider_path!r}."
            )
        seen.add(provider_path)
        normalized.append(provider_path)
    return normalized


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

    normalized = [
        (_normalize_literal_route(p), owner, source) for p, owner, source in mounts
    ]
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


def _route_provider_app_name(routes_module_name: str) -> str:
    return routes_module_name.rsplit(".", maxsplit=1)[0]


def _route_provider_app_is_installed(routes_module_name: str) -> bool:
    app_name = _route_provider_app_name(routes_module_name)
    if not app_name.startswith("apps."):
        return True

    disabled_route_apps = set(getattr(settings, "ROUTE_PROVIDER_DISABLED_APPS", ()))
    if app_name in disabled_route_apps:
        return False

    if django_apps.apps_ready:
        return django_apps.is_installed(app_name)

    return app_name in set(getattr(settings, "INSTALLED_APPS", ()))


def autodiscovered_route_patterns() -> list[URLPattern | URLResolver]:
    """Collect root route patterns from explicitly registered providers."""

    patterns: list[URLPattern | URLResolver] = []
    mounted_prefixes: list[tuple[str, str, str]] = []

    for routes_module_name in _configured_route_provider_modules():
        if not _route_provider_app_is_installed(routes_module_name):
            continue

        routes_module = import_module(routes_module_name)

        root_patterns = getattr(routes_module, "ROOT_URLPATTERNS", None)
        if root_patterns is None:
            raise AttributeError(f"{routes_module_name} must define ROOT_URLPATTERNS")

        patterns.extend(root_patterns)
        owner = _route_provider_app_name(routes_module_name)
        for root_pattern in root_patterns:
            mounted_prefixes.append(
                (str(root_pattern.pattern), owner, routes_module_name)
            )

    _detect_conflicting_roots(mounted_prefixes)

    return patterns


def autodiscovered_websocket_urlpatterns() -> list[URLPattern | URLResolver]:
    """Collect websocket URL patterns from installed ASGI route providers."""

    patterns: list[URLPattern | URLResolver] = []

    for routing_module_name in _configured_route_provider_modules(
        "ASGI_ROUTE_PROVIDERS",
    ):
        if not _route_provider_app_is_installed(routing_module_name):
            continue

        routing_module = import_module(routing_module_name)

        websocket_patterns = getattr(routing_module, "websocket_urlpatterns", None)
        if websocket_patterns is None:
            raise AttributeError(
                f"{routing_module_name} must define websocket_urlpatterns"
            )

        patterns.extend(websocket_patterns)

    return patterns
