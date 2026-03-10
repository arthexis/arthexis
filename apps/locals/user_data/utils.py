from __future__ import annotations

"""Backward-compatible utilities for ``apps.locals.user_data``."""

from apps.locals.utils import safe_next_url


def _safe_next_url(request):
    """Backward-compatible alias for :func:`apps.locals.utils.safe_next_url`."""

    return safe_next_url(request)
