"""Authorization and local-list call result handlers."""

from __future__ import annotations

from . import legacy
from .common import HandlerContext, legacy_adapter


async def send_local_list(ctx: HandlerContext) -> bool:
    """Handle SendLocalList responses.

    Expected payload keys: ``status`` and optional ``currentLocalListVersion``.
    Persistence updates: updates local authorization version state.
    """

    return await legacy.handle_send_local_list_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_local_list_version(ctx: HandlerContext) -> bool:
    """Handle GetLocalListVersion responses.

    Expected payload keys: ``listVersion`` and optional ``localAuthorizationList``.
    Persistence updates: applies authorization entries and updates local authorization version.
    """

    return await legacy.handle_get_local_list_version_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def clear_cache(ctx: HandlerContext) -> bool:
    """Handle ClearCache responses.

    Expected payload keys: ``status``.
    Persistence updates: resets local authorization version when accepted.
    """

    return await legacy.handle_clear_cache_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


handle_send_local_list_result = legacy_adapter(send_local_list)
handle_get_local_list_version_result = legacy_adapter(get_local_list_version)
handle_clear_cache_result = legacy_adapter(clear_cache)
