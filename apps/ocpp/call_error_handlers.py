"""Backward-compatible exports for call error handlers."""
from __future__ import annotations

from .call_handlers import errors as _errors
from .call_handlers.errors import *  # noqa: F403
from .call_handlers.errors import CALL_ERROR_HANDLERS, dispatch_call_error

__all__ = list(_errors.__all__)
