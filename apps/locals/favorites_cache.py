"""Utilities for caching per-user admin favorites blocks."""

from collections.abc import Callable

from .caches import (
    build_cache_key,
    cache_key_boolean_variants,
    get_cached_value_for_key,
    invalidate_cache_keys,
)


def _user_cache_keys(
    user_id: int,
    *,
    show_changelinks: bool | None = None,
    show_model_badges: bool | None = None,
) -> list[str]:
    """Return cache keys for a user's favorites block."""

    base_key = build_cache_key("admin", "favorites", "block", user_id)
    return cache_key_boolean_variants(
        base_key,
        changelinks=show_changelinks,
        model_badges=show_model_badges,
    )


def user_favorites_cache_key(
    user_id: int, *, show_changelinks: bool, show_model_badges: bool
) -> str:
    """Build a cache key for a user's dashboard favorites block."""

    return _user_cache_keys(
        user_id,
        show_changelinks=show_changelinks,
        show_model_badges=show_model_badges,
    )[0]


def get_cached_user_favorites(
    user_id: int,
    *,
    show_changelinks: bool,
    show_model_badges: bool,
    builder: Callable[[], object],
    force_refresh: bool = False,
):
    cache_key = user_favorites_cache_key(
        user_id,
        show_changelinks=show_changelinks,
        show_model_badges=show_model_badges,
    )
    return get_cached_value_for_key(
        cache_key, builder, force_refresh=force_refresh
    )


def clear_user_favorites_cache(
    user, *, show_changelinks: bool | None = None, show_model_badges: bool | None = None
) -> None:
    """Remove cached dashboard favorites blocks for the given user."""

    if not user or not getattr(user, "is_authenticated", False):
        return

    keys = _user_cache_keys(
        user.pk,
        show_changelinks=show_changelinks,
        show_model_badges=show_model_badges,
    )
    invalidate_cache_keys(keys)
