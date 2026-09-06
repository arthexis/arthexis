"""Compatibility module for application-registry Django checks."""

from apps.app.checks import apps_registry as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)

del _name
