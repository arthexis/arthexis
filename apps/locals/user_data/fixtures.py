"""Backward-compatible imports for user fixture helpers."""

from apps.locals import user_fixtures as _user_fixtures

globals().update(
    {
        name: getattr(_user_fixtures, name)
        for name in dir(_user_fixtures)
        if not name.startswith("__")
    }
)
