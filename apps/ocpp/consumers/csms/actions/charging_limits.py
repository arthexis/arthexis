"""Charging limit related CSMS action handlers."""

from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.services import report_persistence


class ClearedChargingLimitActionHandler:
    """Handle ClearedChargingLimit payloads."""

    def __init__(self, consumer) -> None:
        self.consumer = consumer

    async def handle(self, payload, msg_id, _raw, _text_data) -> dict:
        payload_data = payload if isinstance(payload, dict) else {}
        evse_id_value = payload_data.get("evseId")
        try:
            evse_id = int(evse_id_value) if evse_id_value is not None else None
        except (TypeError, ValueError):
            evse_id = None
        source_value = str(payload_data.get("chargingLimitSource") or "").strip()

        details: list[str] = []
        if source_value:
            details.append(f"source={source_value}")
        if evse_id is not None:
            details.append(f"evseId={evse_id}")
        message = "ClearedChargingLimit"
        if details:
            message += f": {', '.join(details)}"
        store.add_log(self.consumer.store_key, message, log_type="charger")

        await database_sync_to_async(report_persistence.persist_cleared_charging_limit_event)(
            charger=self.consumer.charger,
            aggregate_charger=self.consumer.aggregate_charger,
            charger_id=getattr(self.consumer, "charger_id", None),
            connector_id=getattr(self.consumer, "connector_value", None),
            msg_id=msg_id,
            evse_id=evse_id,
            source_value=source_value,
            payload_data=payload_data,
        )
        return {}


class NotifyChargingLimitActionHandler:
    """Handle NotifyChargingLimit payloads."""

    def __init__(self, consumer) -> None:
        self.consumer = consumer

    async def handle(self, payload, _msg_id, _raw, _text_data) -> dict:
        payload_data = payload if isinstance(payload, dict) else {}
        charging_limit = payload_data.get("chargingLimit")
        if not isinstance(charging_limit, dict):
            charging_limit = {}
        source_value = str(charging_limit.get("chargingLimitSource") or "").strip()
        grid_critical_value = charging_limit.get("isGridCritical")
        grid_critical = bool(grid_critical_value) if grid_critical_value is not None else None

        schedules = payload_data.get("chargingSchedule")
        if not isinstance(schedules, list):
            schedules = []
        evse_id_value = payload_data.get("evseId")
        try:
            evse_id = int(evse_id_value) if evse_id_value is not None else None
        except (TypeError, ValueError):
            evse_id = None

        details: list[str] = []
        if source_value:
            details.append(f"source={source_value}")
        if grid_critical is not None:
            details.append(f"gridCritical={'yes' if grid_critical else 'no'}")
        if evse_id is not None:
            details.append(f"evseId={evse_id}")
        if schedules:
            details.append(f"schedules={len(schedules)}")
        message = "NotifyChargingLimit"
        if details:
            message += f": {', '.join(details)}"
        store.add_log(self.consumer.store_key, message, log_type="charger")

        normalized_payload: dict[str, object] = {
            "chargingLimit": charging_limit,
            "chargingSchedule": schedules,
        }
        if evse_id is not None:
            normalized_payload["evseId"] = evse_id

        await database_sync_to_async(report_persistence.persist_notify_charging_limit)(
            charger=self.consumer.charger,
            aggregate_charger=self.consumer.aggregate_charger,
            charger_id=getattr(self.consumer, "charger_id", None),
            connector_id=getattr(self.consumer, "connector_value", None),
            normalized_payload=normalized_payload,
            source_value=source_value,
            grid_critical=grid_critical,
            received_at=timezone.now(),
        )
        return {}
