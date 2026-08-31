"""Metering action handlers for CSMS consumer."""

from __future__ import annotations

from channels.db import database_sync_to_async

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from apps.ocpp.consumers.csms import persistence


class MeteringHandlersMixin:
    """Handle MeterValues actions and related persistence."""

    def _normalized_meter_values_payload(self, payload: object) -> dict:
        """Return legacy-compatible MeterValues payload keys for OCPP 2.1 parity."""

        payload_data = payload if isinstance(payload, dict) else {}
        normalized = dict(payload_data)
        if normalized.get("connectorId") is None:
            evse_data = payload_data.get("evse")
            connector_id = None
            if isinstance(evse_data, dict):
                connector_id = evse_data.get("connectorId")
                if connector_id is None:
                    connector_id = evse_data.get("id")
            if connector_id is None:
                connector_id = payload_data.get("evseId")
            if connector_id is not None:
                normalized["connectorId"] = connector_id
        return normalized

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "MeterValues")
    async def _handle_meter_values_legacy(self, payload, _msg_id, _raw, text_data):
        payload_data = self._normalized_meter_values_payload(payload)
        await self._store_meter_values(payload_data, text_data)
        self.charger.last_meter_values = payload_data
        await database_sync_to_async(persistence.persist_legacy_meter_values)(
            charger_pk=self.charger.pk,
            payload=payload_data,
        )
        return {}
