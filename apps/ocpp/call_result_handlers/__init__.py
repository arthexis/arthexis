"""Facade and compatibility exports for OCPP call result handling."""

from __future__ import annotations

from .authorization import *
from .certificates import *
from .common import CallResultContext, HandlerContext, build_context
from .configuration import *
from .diagnostics import *
from .firmware import *
from .profiles import *
from .registry import CALL_RESULT_HANDLER_REGISTRY, build_legacy_registry
from .transactions import *

CALL_RESULT_HANDLERS = build_legacy_registry()


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
    context = build_context(consumer, message_id, metadata, payload_data, log_key)
    return await handler(context)
