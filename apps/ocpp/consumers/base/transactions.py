"""Transaction handling helpers for OCPP sessions.

These helpers wrap high-risk transaction actions (Start/Stop/TransactionEvent)
for OCPP 1.6 and 2.x. Database side effects are delegated to consumer methods
that persist ``Transaction`` rows and related session metadata.
"""

from typing import Protocol

from apps.ocpp.payload_types import HandlerPayload, HandlerResponse


class TransactionConsumer(Protocol):
    async def _handle_transaction_event_legacy(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse: ...

    async def _handle_start_transaction_legacy(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse: ...

    async def _handle_stop_transaction_legacy(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse: ...


class TransactionHandler:
    """Adapter that groups transaction-related call handlers.

    The handler assumes the wrapped consumer exposes the legacy transaction
    coroutines that perform DB writes on ``apps.ocpp.models.Transaction`` and
    related entities.
    """

    def __init__(self, consumer: TransactionConsumer) -> None:
        self.consumer = consumer

    async def handle_transaction_event(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse:
        """Handle OCPP 2.x ``TransactionEvent`` messages with DB persistence."""

        return await self.consumer._handle_transaction_event_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_start_transaction(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse:
        """Handle OCPP 1.6 ``StartTransaction`` and persist transaction rows."""

        return await self.consumer._handle_start_transaction_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_stop_transaction(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse:
        """Handle OCPP 1.6 ``StopTransaction`` and finalize transaction rows."""

        return await self.consumer._handle_stop_transaction_legacy(
            payload, msg_id, raw, text_data
        )
