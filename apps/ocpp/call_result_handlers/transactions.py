"""Transaction and reservation result handlers."""

from __future__ import annotations

from . import legacy
from .common import HandlerContext, legacy_adapter


async def reserve_now(ctx: HandlerContext) -> bool:
    """Handle ReserveNow responses.

    Expected payload keys: ``status``.
    Persistence updates: updates ``CPReservation`` EVCS confirmation fields.
    """

    return await legacy.handle_reserve_now_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def cancel_reservation(ctx: HandlerContext) -> bool:
    """Handle CancelReservation responses.

    Expected payload keys: ``status``.
    Persistence updates: updates ``CPReservation`` status and clears confirmation fields.
    """

    return await legacy.handle_cancel_reservation_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def remote_start_transaction(ctx: HandlerContext) -> bool:
    """Handle RemoteStartTransaction responses.

    Expected payload keys: ``status``.
    Persistence updates: log and pending call result.
    """

    return await legacy.handle_remote_start_transaction_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def remote_stop_transaction(ctx: HandlerContext) -> bool:
    """Handle RemoteStopTransaction responses.

    Expected payload keys: ``status``.
    Persistence updates: log and pending call result.
    """

    return await legacy.handle_remote_stop_transaction_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def request_start_transaction(ctx: HandlerContext) -> bool:
    """Handle RequestStartTransaction responses.

    Expected payload keys: ``status`` and optional ``transactionId``.
    Persistence updates: updates in-memory transaction request status.
    """

    return await legacy.handle_request_start_transaction_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def request_stop_transaction(ctx: HandlerContext) -> bool:
    """Handle RequestStopTransaction responses.

    Expected payload keys: ``status``.
    Persistence updates: updates in-memory transaction request status.
    """

    return await legacy.handle_request_stop_transaction_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_transaction_status(ctx: HandlerContext) -> bool:
    """Handle GetTransactionStatus responses.

    Expected payload keys: optional ``ongoingIndicator`` and ``messagesInQueue``.
    Persistence updates: log and pending call result.
    """

    return await legacy.handle_get_transaction_status_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


handle_reserve_now_result = legacy_adapter(reserve_now)
handle_cancel_reservation_result = legacy_adapter(cancel_reservation)
handle_remote_start_transaction_result = legacy_adapter(remote_start_transaction)
handle_remote_stop_transaction_result = legacy_adapter(remote_stop_transaction)
handle_request_start_transaction_result = legacy_adapter(request_start_transaction)
handle_request_stop_transaction_result = legacy_adapter(request_stop_transaction)
handle_get_transaction_status_result = legacy_adapter(get_transaction_status)
