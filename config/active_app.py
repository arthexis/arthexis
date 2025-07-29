import threading

_active = threading.local()
_active.name = "website"


def get_active_app():
    """Return the currently active app name."""
    return getattr(_active, "name", "website")


def set_active_app(name: str) -> None:
    """Set the active app name for the current thread."""
    _active.name = name or "website"
