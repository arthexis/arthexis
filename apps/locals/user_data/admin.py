"""Backward-compatible imports for local admin mixins."""

from apps.locals import admin_mixins as _admin_mixins

globals().update(
    {
        name: getattr(_admin_mixins, name)
        for name in dir(_admin_mixins)
        if not name.startswith("__")
    }
)
