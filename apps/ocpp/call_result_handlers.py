"""Backward-compatible exports for call result handlers."""
from __future__ import annotations

from .call_handlers import results as _results
from .call_handlers.results import *  # noqa: F403
from .call_handlers.results import CALL_RESULT_HANDLERS, dispatch_call_result

__all__ = list(_results.__all__)
