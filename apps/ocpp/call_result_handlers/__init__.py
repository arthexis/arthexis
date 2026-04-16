"""Minimal package API for OCPP call result handling."""

from __future__ import annotations

from .common import CallResultContext, build_context as _build_context
from .registry import CALL_RESULT_HANDLER_REGISTRY


async def dispatch_call_result(
    consumer: CallResultContext,
    action: str | None,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    """Dispatch a call result using the central registry.

    Keeps the legacy import path and function signature stable while routing
    through context-based handlers.
    """

    if not action:
        return False
    handler = CALL_RESULT_HANDLER_REGISTRY.get(action)
    if not handler:
        return False
    context = _build_context(consumer, message_id, metadata, payload_data, log_key)
    return await handler(context)


__all__ = [
    "CALL_RESULT_HANDLER_REGISTRY",
    "CallResultContext",
    "dispatch_call_result",
]
