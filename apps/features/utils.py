"""Utilities for querying suite feature state."""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.db.utils import OperationalError, ProgrammingError

from .models import Feature
from .parameters import get_feature_parameter

PAGES_CHAT_FEATURE_SLUG = "pages-chat"
QUICK_WEB_SHARE_FEATURE_SLUG = "quick-web-share"
STAFF_CHAT_BRIDGE_FEATURE_SLUG = "staff-chat-bridge"


def is_suite_feature_enabled(slug: str, *, default: bool = True) -> bool:
    """Return whether the suite feature ``slug`` is currently enabled.

    The lookup is defensive so early bootstrapping states (for example during
    migrations before the ``features_feature`` table exists) gracefully fall
    back to ``default``.

    Parameters:
        slug: Feature slug to inspect.
        default: Fallback value when the feature table is unavailable or missing.

    Returns:
        bool: Whether the suite feature is enabled.
    """

    try:
        is_enabled = (
            Feature.objects.filter(slug=slug)
            .values_list("is_enabled", flat=True)
            .first()
        )
    except (OperationalError, ProgrammingError):
        return default
    if is_enabled is None:
        return default
    return bool(is_enabled)


def get_cached_feature_enabled(
    slug: str,
    *,
    cache_key: str,
    timeout: int = 300,
    default: bool = False,
) -> bool:
    """Return feature-enabled state with a shared cache strategy."""

    cached = cache.get(cache_key)
    if isinstance(cached, bool):
        return cached
    enabled = is_suite_feature_enabled(slug, default=default)
    cache.set(cache_key, enabled, timeout=timeout)
    return enabled


def get_cached_feature_parameter(
    slug: str,
    key: str,
    *,
    cache_key: str,
    timeout: int = 300,
    fallback: str = "",
) -> str:
    """Return feature parameter value with cache-backed reads."""

    cached = cache.get(cache_key)
    if isinstance(cached, str):
        return cached
    value = get_feature_parameter(slug, key, fallback=fallback)
    cache.set(cache_key, value, timeout=timeout)
    return value


def is_pages_chat_enabled(*, default: bool = False) -> bool:
    """Return whether pages chat is enabled at the product level.

    Parameters:
        default: Fallback value when suite feature state cannot be loaded.

    Returns:
        bool: ``True`` when the ``pages-chat`` suite feature is enabled.
    """

    return is_suite_feature_enabled(PAGES_CHAT_FEATURE_SLUG, default=default)


def is_pages_chat_runtime_enabled(*, default: bool = False) -> bool:
    """Return whether pages chat may run in the current deployment.

    Parameters:
        default: Fallback product-level value when feature storage is unavailable.

    Returns:
        bool: ``True`` when the deployment setting and suite feature both allow
        public pages chat to run.
    """

    return bool(
        getattr(settings, "PAGES_CHAT_ENABLED", False)
    ) and is_pages_chat_enabled(default=default)


__all__ = [
    "PAGES_CHAT_FEATURE_SLUG",
    "QUICK_WEB_SHARE_FEATURE_SLUG",
    "STAFF_CHAT_BRIDGE_FEATURE_SLUG",
    "get_cached_feature_enabled",
    "get_cached_feature_parameter",
    "is_pages_chat_enabled",
    "is_pages_chat_runtime_enabled",
    "is_suite_feature_enabled",
]
