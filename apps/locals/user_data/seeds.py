"""Backward-compatible imports for local seed helpers."""

from apps.locals import seeds as _seeds

globals().update(
    {name: getattr(_seeds, name) for name in dir(_seeds) if not name.startswith("__")}
)
