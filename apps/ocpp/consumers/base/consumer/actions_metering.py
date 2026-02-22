"""Protocol action mixin for metering-related OCPP calls."""

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from .metering import MeteringHandler


class MeteringActionsMixin:
    """Expose protocol-routed metering action handlers."""

    def _metering_handler(self) -> MeteringHandler:
        """Return metering helper for OCPP meter sample persistence."""
        handler = getattr(self, "_cached_metering_handler", None)
        if handler is None:
            handler = MeteringHandler(self)
            self._cached_metering_handler = handler
        return handler

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    async def _handle_meter_values_action(self, payload, msg_id, raw, text_data):
        """Route OCPP meter values through the metering handler."""
        return await self._metering_handler().handle_meter_values(
            payload, msg_id, raw, text_data
        )
