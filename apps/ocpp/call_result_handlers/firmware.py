"""Firmware and transfer-related call result handlers."""

from __future__ import annotations

from . import legacy
from .common import HandlerContext, legacy_adapter


async def data_transfer(ctx: HandlerContext) -> bool:
    """Handle DataTransfer responses.

    Expected payload keys: ``status`` and optional ``data``.
    Persistence updates: updates ``DataTransferMessage`` and related firmware request state.
    """

    return await legacy.handle_data_transfer_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def update_firmware(ctx: HandlerContext) -> bool:
    """Handle UpdateFirmware responses.

    Expected payload keys: optional ``status``.
    Persistence updates: marks ``CPFirmwareDeployment`` status and response payload.
    """

    return await legacy.handle_update_firmware_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def publish_firmware(ctx: HandlerContext) -> bool:
    """Handle PublishFirmware responses.

    Expected payload keys: optional ``status``.
    Persistence updates: marks ``CPFirmwareDeployment`` status and response payload.
    """

    return await legacy.handle_publish_firmware_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def unpublish_firmware(ctx: HandlerContext) -> bool:
    """Handle UnpublishFirmware responses.

    Expected payload keys: ``status``.
    Persistence updates: log and pending call result.
    """

    return await legacy.handle_unpublish_firmware_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


handle_data_transfer_result = legacy_adapter(data_transfer)
handle_update_firmware_result = legacy_adapter(update_firmware)
handle_publish_firmware_result = legacy_adapter(publish_firmware)
handle_unpublish_firmware_result = legacy_adapter(unpublish_firmware)
