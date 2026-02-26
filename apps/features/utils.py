"""Utilities for querying suite feature state."""

from __future__ import annotations

from django.db.utils import OperationalError, ProgrammingError

from .models import Feature


def is_suite_feature_enabled(slug: str, *, default: bool = True) -> bool:
    """Return whether the suite feature ``slug`` is currently enabled.

    The lookup is defensive so early bootstrapping states (for example during
    migrations before the ``features_feature`` table exists) gracefully fall
    back to ``default``.
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


__all__ = ["is_suite_feature_enabled"]
