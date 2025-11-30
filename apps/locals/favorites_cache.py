"""Utilities for caching per-user admin favorites blocks."""

from collections.abc import Iterable

from django.core.cache import cache


def _user_cache_keys(user_id: int, *, show_changelinks: bool | None = None, show_model_badges: bool | None = None) -> list[str]:
    """Return cache keys for a user's favorites block.

    If ``show_changelinks`` or ``show_model_badges`` are ``None``, keys for both
    boolean states are returned to support cache invalidation across variants.
    """

    changelinks_options: Iterable[bool]
    if show_changelinks is None:
        changelinks_options = (False, True)
    else:
        changelinks_options = (bool(show_changelinks),)

    model_badges_options: Iterable[bool]
    if show_model_badges is None:
        model_badges_options = (False, True)
    else:
        model_badges_options = (bool(show_model_badges),)

    return [
        f"admin:favorites:block:{user_id}:{int(changelinks)}:{int(model_badges)}"
        for changelinks in changelinks_options
        for model_badges in model_badges_options
    ]


def user_favorites_cache_key(
    user_id: int, *, show_changelinks: bool, show_model_badges: bool
) -> str:
    """Build a cache key for a user's dashboard favorites block."""

    return _user_cache_keys(
        user_id,
        show_changelinks=show_changelinks,
        show_model_badges=show_model_badges,
    )[0]


def clear_user_favorites_cache(user, *, show_changelinks: bool | None = None, show_model_badges: bool | None = None) -> None:
    """Remove cached dashboard favorites blocks for the given user."""

    if not user or not getattr(user, "is_authenticated", False):
        return
    cache.delete_many(
        _user_cache_keys(
            user.pk,
            show_changelinks=show_changelinks,
            show_model_badges=show_model_badges,
        )
    )
