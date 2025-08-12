import threading
import socket

_active = threading.local()
_active.name = socket.gethostname()


def get_active_app():
    """Return the currently active app name."""
    return getattr(_active, "name", socket.gethostname())


def set_active_app(name: str) -> None:
    """Set the active app name for the current thread."""
    _active.name = name or socket.gethostname()
