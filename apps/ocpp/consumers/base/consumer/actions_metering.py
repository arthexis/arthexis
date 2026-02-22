"""Metering protocol action mixin for the CSMS consumer."""

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel


class MeteringActionsMixin:
    """Expose metering action handlers while preserving protocol decorators."""

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    async def _handle_meter_values_action(self, payload, msg_id, raw, text_data):
        """Route OCPP meter values through the metering handler."""

        return await self._metering_handler().handle_meter_values(
            payload, msg_id, raw, text_data
        )
