"""Configuration and generic control call result handlers."""

from __future__ import annotations

from . import legacy
from .common import HandlerContext, legacy_adapter


async def change_configuration(ctx: HandlerContext) -> bool:
    """Handle ChangeConfiguration responses.

    Expected payload keys: ``status``.
    Persistence updates: stores pending call result and may update ``ChargerConfiguration`` snapshot.
    """

    return await legacy.handle_change_configuration_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_configuration(ctx: HandlerContext) -> bool:
    """Handle GetConfiguration responses.

    Expected payload keys: arbitrary charger key/value listing.
    Persistence updates: stores pending call result and persists current ``ChargerConfiguration`` values.
    """

    return await legacy.handle_get_configuration_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def trigger_message(ctx: HandlerContext) -> bool:
    """Handle TriggerMessage responses.

    Expected payload keys: ``status``.
    Persistence updates: records pending result and may register follow-up trigger workflow.
    """

    return await legacy.handle_trigger_message_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def reset(ctx: HandlerContext) -> bool:
    """Handle Reset responses.

    Expected payload keys: ``status``.
    Persistence updates: log and pending call result only.
    """

    return await legacy.handle_reset_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def change_availability(ctx: HandlerContext) -> bool:
    """Handle ChangeAvailability responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: pending call result and consumer availability state update.
    """

    return await legacy.handle_change_availability_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def unlock_connector(ctx: HandlerContext) -> bool:
    """Handle UnlockConnector responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: logs and pending call result.
    """

    return await legacy.handle_unlock_connector_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def set_network_profile(ctx: HandlerContext) -> bool:
    """Handle SetNetworkProfile responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: marks ``CPNetworkProfileDeployment`` status for the request metadata.
    """

    return await legacy.handle_set_network_profile_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


handle_change_configuration_result = legacy_adapter(change_configuration)
handle_get_configuration_result = legacy_adapter(get_configuration)
handle_trigger_message_result = legacy_adapter(trigger_message)
handle_reset_result = legacy_adapter(reset)
handle_change_availability_result = legacy_adapter(change_availability)
handle_unlock_connector_result = legacy_adapter(unlock_connector)

handle_set_network_profile_result = legacy_adapter(set_network_profile)
