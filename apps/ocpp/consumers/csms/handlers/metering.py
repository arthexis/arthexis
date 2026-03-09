"""Metering action handlers for CSMS consumer."""

from __future__ import annotations

from channels.db import database_sync_to_async

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from apps.ocpp.consumers.csms import persistence


class MeteringHandlersMixin:
    """Handle MeterValues actions and related persistence."""

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    async def _handle_meter_values_legacy(self, payload, _msg_id, _raw, text_data):
        await self._store_meter_values(payload, text_data)
        self.charger.last_meter_values = payload
        await database_sync_to_async(persistence.persist_legacy_meter_values)(
            charger_pk=self.charger.pk,
            payload=payload,
        )
        return {}
