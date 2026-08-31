"""Diagnostics and log retrieval call result handlers."""

from __future__ import annotations

from . import legacy
from .common import HandlerContext, legacy_adapter


async def get_log(ctx: HandlerContext) -> bool:
    """Handle GetLog responses.

    Expected payload keys: ``status`` plus optional ``filename``/``location`` and ``logData`` fragments.
    Persistence updates: updates ``ChargerLogRequest`` and log capture session state.
    """

    return await legacy.handle_get_log_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_diagnostics(ctx: HandlerContext) -> bool:
    """Handle GetDiagnostics responses.

    Expected payload keys: ``status`` plus optional ``fileName``/``filename``/``location``.
    Persistence updates: updates charger diagnostics timestamp/location.
    """

    return await legacy.handle_get_diagnostics_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


handle_get_log_result = legacy_adapter(get_log)
handle_get_diagnostics_result = legacy_adapter(get_diagnostics)
