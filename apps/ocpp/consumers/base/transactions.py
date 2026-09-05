"""Transaction handling helpers for OCPP sessions.

These helpers wrap high-risk transaction actions (Start/Stop/TransactionEvent)
for OCPP 1.6 and 2.x. Database side effects are delegated to consumer methods
that persist ``Transaction`` rows and related session metadata.
"""

from typing import Any, Protocol

from channels.db import database_sync_to_async

from apps.ocpp.models import Transaction
from apps.ocpp.payload_types import HandlerPayload, HandlerResponse

from .historical_transactions import (
    HISTORICAL_REPLAY_ID_PREFIX,
    reconcile_historical_start_transaction,
)


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


async def _latest_rejected_transaction_id(
    consumer: Any,
    payload: HandlerPayload,
) -> int | None:
    """Return the rejected transaction just persisted by the legacy 1.6 handler."""

    charger = getattr(consumer, "charger", None)
    if charger is None:
        return None
    id_tag = str(payload.get("idTag") or "")

    def _lookup() -> int | None:
        return (
            Transaction.objects.filter(
                charger=charger,
                rfid=id_tag,
                authorization_status=Transaction.AuthorizationStatus.REJECTED,
                rejected_at__isnull=False,
            )
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first()
        )

    return await database_sync_to_async(_lookup)()


async def _historical_replay_identity_for_stop(
    consumer: Any,
    payload: HandlerPayload,
) -> tuple[int | None, str]:
    """Capture a historical replay identity before legacy stop handling mutates it."""

    charger = getattr(consumer, "charger", None)
    raw_tx_id = payload.get("transactionId")
    if charger is None or raw_tx_id is None:
        return None, ""
    try:
        transaction_id = int(raw_tx_id)
    except (TypeError, ValueError):
        return None, ""

    def _lookup() -> str:
        return (
            Transaction.objects.filter(pk=transaction_id, charger=charger)
            .values_list("ocpp_transaction_id", flat=True)
            .first()
            or ""
        )

    replay_identity = await database_sync_to_async(_lookup)()
    if not replay_identity.startswith(HISTORICAL_REPLAY_ID_PREFIX):
        return None, ""
    return transaction_id, replay_identity


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

        historical_response = await reconcile_historical_start_transaction(
            self.consumer, payload, msg_id, text_data
        )
        if historical_response is not None:
            return historical_response

        response = await self.consumer._handle_start_transaction_legacy(
            payload, msg_id, raw, text_data
        )
        id_tag_info = response.get("idTagInfo") if isinstance(response, dict) else None
        if (
            isinstance(response, dict)
            and "transactionId" not in response
            and isinstance(id_tag_info, dict)
            and id_tag_info.get("status") == "Invalid"
        ):
            transaction_id = await _latest_rejected_transaction_id(
                self.consumer, payload
            )
            if transaction_id is not None:
                response = {**response, "transactionId": transaction_id}
        return response

    async def handle_stop_transaction(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse:
        """Handle OCPP 1.6 ``StopTransaction`` and finalize transaction rows."""

        historical_pk, replay_identity = await _historical_replay_identity_for_stop(
            self.consumer, payload
        )
        response = await self.consumer._handle_stop_transaction_legacy(
            payload, msg_id, raw, text_data
        )
        if historical_pk is not None and replay_identity:
            await database_sync_to_async(
                Transaction.objects.filter(pk=historical_pk).update
            )(ocpp_transaction_id=replay_identity)
        return response
