"""Status and availability action handlers for CSMS consumer."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone as dt_timezone

from channels.db import database_sync_to_async
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.ocpp import store
from apps.ocpp.models import Charger
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from apps.ocpp.consumers.csms import persistence


logger = logging.getLogger(__name__)


class StatusHandlersMixin:
    """Handle heartbeat and status notification actions."""

    def _normalized_status_notification_payload(self, payload: object) -> dict:
        """Return legacy-compatible status payload keys for OCPP 2.1 parity."""

        payload_data = payload if isinstance(payload, dict) else {}
        normalized = dict(payload_data)

        connector_id = payload_data.get("connectorId")
        if connector_id is None:
            evse_data = payload_data.get("evse")
            if isinstance(evse_data, dict):
                connector_id = evse_data.get("connectorId") or evse_data.get("id")
        if connector_id is None:
            connector_id = payload_data.get("evseId")
        if connector_id is not None:
            normalized["connectorId"] = connector_id

        status_value = payload_data.get("status")
        if status_value is None:
            status_value = payload_data.get("connectorStatus")
        if status_value is not None:
            normalized["status"] = status_value

        if (
            normalized.get("errorCode") is None
            and payload_data.get("connectorStatus") is not None
        ):
            normalized["errorCode"] = "NoError"

        status_info = payload_data.get("statusInfo")
        if isinstance(status_info, dict):
            if not normalized.get("info"):
                normalized["info"] = status_info.get("additionalInfo")
            if not normalized.get("vendorId"):
                normalized["vendorId"] = status_info.get("reasonCode")

        return normalized

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "Heartbeat")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "Heartbeat")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "Heartbeat")
    async def _handle_heartbeat_action(self, payload, msg_id, raw, text_data):
        current_time = datetime.now(dt_timezone.utc).isoformat().replace("+00:00", "Z")
        reply_payload = {"currentTime": current_time}
        now = timezone.now()
        self.charger.last_heartbeat = now
        if self.aggregate_charger and self.aggregate_charger is not self.charger:
            self.aggregate_charger.last_heartbeat = now
        await database_sync_to_async(Charger.objects.filter(charger_id=self.charger_id).update)(
            last_heartbeat=now
        )
        return reply_payload

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "StatusNotification")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "StatusNotification")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "StatusNotification")
    async def _handle_status_notification_action(self, payload, msg_id, raw, text_data):
        payload_data = self._normalized_status_notification_payload(payload)
        await self._assign_connector(payload_data.get("connectorId"))
        status = (payload_data.get("status") or "").strip()
        error_code = (payload_data.get("errorCode") or "").strip()
        vendor_info = {
            key: value
            for key, value in (
                ("info", payload_data.get("info")),
                ("vendorId", payload_data.get("vendorId")),
            )
            if value
        }
        vendor_value = vendor_info or None
        timestamp_raw = payload_data.get("timestamp")
        status_timestamp = parse_datetime(timestamp_raw) if timestamp_raw else None
        if status_timestamp is None:
            status_timestamp = timezone.now()
        elif timezone.is_naive(status_timestamp):
            status_timestamp = timezone.make_aware(status_timestamp)
        update_kwargs = {
            "last_status": status,
            "last_error_code": error_code,
            "last_status_vendor_info": vendor_value,
            "last_status_timestamp": status_timestamp,
        }
        connector_value = payload_data.get("connectorId")
        await database_sync_to_async(persistence.update_status_notification_records)(
            charger_id=self.charger_id,
            connector_value=connector_value,
            primary_charger=self.charger,
            aggregate_charger=self.aggregate_charger,
            update_kwargs=update_kwargs,
        )
        try:
            await database_sync_to_async(persistence.sync_charger_error_security_event)(
                charger_id=self.charger_id,
                connector_value=connector_value,
                status=status,
                error_code=error_code,
                status_timestamp=status_timestamp,
            )
        except Exception:
            active_logger = getattr(self, "logger", logger)
            active_logger.exception(
                "Failed to sync charger security alert event for charger_id=%s connector=%s",
                self.charger_id,
                connector_value,
            )
        if connector_value is not None and status.lower() == "available":
            tx_obj = store.transactions.pop(self.store_key, None)
            if tx_obj:
                await self._cancel_consumption_message()
                store.end_session_log(self.store_key)
                store.stop_session_lock()
        store.add_log(
            self.store_key,
            f"StatusNotification processed: {json.dumps(payload_data, sort_keys=True)}",
            log_type="charger",
        )
        availability_state = Charger.availability_state_from_status(status)
        if availability_state:
            await self._update_availability_state(
                availability_state,
                status_timestamp,
                self.connector_value,
            )
        return {}

    async def _update_availability_state(self, state: str, timestamp, connector_value: int | None) -> None:
        """Persist availability state for the current charger and cached references."""

        targets = await database_sync_to_async(persistence.update_availability_state_records)(
            charger_id=self.charger_id,
            connector_value=connector_value,
            state=state,
            timestamp=timestamp,
        )
        updates = {
            "availability_state": state,
            "availability_state_updated_at": timestamp,
        }
        for target in targets:
            if self.charger and self.charger.pk == target.pk:
                for field, value in updates.items():
                    setattr(self.charger, field, value)
            if self.aggregate_charger and self.aggregate_charger.pk == target.pk:
                for field, value in updates.items():
                    setattr(self.aggregate_charger, field, value)
