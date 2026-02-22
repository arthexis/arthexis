"""Transaction protocol action mixin for the CSMS consumer."""

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel


class TransactionActionsMixin:
    """Expose transaction action handlers while preserving protocol decorators."""

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
