"""Compatibility module for the app-owned ``enabled_apps_lock`` command."""

from apps.app.management.commands import enabled_apps_lock as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)

del _name
