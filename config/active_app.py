import socket
from contextvars import ContextVar, Token

_HOSTNAME = socket.gethostname()
_ACTIVE_APP: ContextVar[str] = ContextVar("active_app", default=_HOSTNAME)


def get_active_app():
    """Return the currently active app name."""
    return _ACTIVE_APP.get()


def set_active_app(name: str) -> Token[str]:
    """Set the active app name for the current execution context."""
    return _ACTIVE_APP.set(name or _HOSTNAME)


def reset_active_app(token: Token[str]) -> None:
    """Restore the active app name using a token returned by ``set_active_app``."""
    _ACTIVE_APP.reset(token)
