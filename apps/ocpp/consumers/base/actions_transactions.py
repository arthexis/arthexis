"""Protocol action mixin for transaction-oriented OCPP calls."""

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from .transactions import TransactionHandler


class TransactionActionsMixin:
    """Expose protocol-routed transaction action handlers."""

    def _transaction_handler(self) -> TransactionHandler:
        """Return transaction helper for OCPP 1.6/2.x transaction flows."""
        handler = getattr(self, "_cached_transaction_handler", None)
        if handler is None:
            handler = TransactionHandler(self)
            self._cached_transaction_handler = handler
        return handler

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "TransactionEvent")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "TransactionEvent")
    async def _handle_transaction_event_action(self, payload, msg_id, raw, text_data):
        """Route TransactionEvent through the transaction handler."""
        return await self._transaction_handler().handle_transaction_event(
            payload, msg_id, raw, text_data
        )

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "StartTransaction")
    async def _handle_start_transaction_action(self, payload, msg_id, raw, text_data):
        """Route StartTransaction through the transaction handler."""
        return await self._transaction_handler().handle_start_transaction(
            payload, msg_id, raw, text_data
        )

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "StopTransaction")
    async def _handle_stop_transaction_action(self, payload, msg_id, raw, text_data):
        """Route StopTransaction through the transaction handler."""
        return await self._transaction_handler().handle_stop_transaction(
            payload, msg_id, raw, text_data
        )
