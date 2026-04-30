"""Utilities for querying suite feature state."""

from __future__ import annotations

from weakref import WeakKeyDictionary

from django.core.cache import cache
from django.db import connection
from django.db.utils import OperationalError, ProgrammingError

from .models import Feature
from .parameters import get_feature_parameter

QUICK_WEB_SHARE_FEATURE_SLUG = "quick-web-share"
_CONFIRMED_FEATURE_TABLES: WeakKeyDictionary[object, set[tuple[str, str]]] = (
    WeakKeyDictionary()
)


def _active_atomic_feature_table_cache_key() -> tuple[object | None, tuple[str, str]]:
    atomic_blocks = getattr(connection, "atomic_blocks", ())
    table_key = (connection.alias, Feature._meta.db_table)
    if not atomic_blocks:
        return None, table_key
    return atomic_blocks[-1], table_key


def _confirmed_feature_tables(atomic_block: object) -> set[tuple[str, str]] | None:
    try:
        return _CONFIRMED_FEATURE_TABLES.get(atomic_block)
    except TypeError:
        return None


def _confirm_feature_table(
    atomic_block: object,
    table_key: tuple[str, str],
) -> None:
    try:
        _CONFIRMED_FEATURE_TABLES.setdefault(atomic_block, set()).add(table_key)
    except TypeError:
        return


def _feature_table_available_for_atomic_lookup() -> bool:
    """Avoid dirtying atomic migrations with a query against a missing table."""

    if not connection.in_atomic_block:
        return True
    atomic_block, table_key = _active_atomic_feature_table_cache_key()
    confirmed_tables = (
        _confirmed_feature_tables(atomic_block) if atomic_block is not None else None
    )
    if confirmed_tables is not None and table_key in confirmed_tables:
        return True
    try:
        table_exists = Feature._meta.db_table in connection.introspection.table_names()
    except (OperationalError, ProgrammingError):
        return False
    if table_exists and atomic_block is not None:
        _confirm_feature_table(atomic_block, table_key)
    return table_exists


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

    if not _feature_table_available_for_atomic_lookup():
        return default

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


__all__ = [
    "QUICK_WEB_SHARE_FEATURE_SLUG",
    "get_cached_feature_enabled",
    "get_cached_feature_parameter",
    "is_suite_feature_enabled",
]
