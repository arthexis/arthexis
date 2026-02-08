"""Entry points for OCPP call result and error handlers."""
from __future__ import annotations

from .errors import CALL_ERROR_HANDLERS, dispatch_call_error
from .results import CALL_RESULT_HANDLERS, dispatch_call_result

__all__ = [
    "CALL_ERROR_HANDLERS",
    "CALL_RESULT_HANDLERS",
    "dispatch_call_error",
    "dispatch_call_result",
]
