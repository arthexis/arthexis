"""Monitoring report handlers for CSMS actions."""

from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.services import report_persistence
from apps.ocpp.utils import _parse_ocpp_timestamp, try_parse_int


class NotifyMonitoringReportActionHandler:
    """Handle NotifyMonitoringReport payloads."""

    def __init__(self, consumer) -> None:
        self.consumer = consumer

    async def handle(self, payload, _msg_id, _raw, _text_data) -> dict:
        payload_data = payload if isinstance(payload, dict) else {}
        request_id_value = payload_data.get("requestId")
        seq_no_value = payload_data.get("seqNo")
        generated_at = _parse_ocpp_timestamp(payload_data.get("generatedAt"))
        tbc_value = payload_data.get("tbc")
        request_id = try_parse_int(request_id_value)
        seq_no = try_parse_int(seq_no_value)
        tbc = bool(tbc_value) if tbc_value is not None else False
        monitoring_data = payload_data.get("monitoringData")
        if not isinstance(monitoring_data, (list, tuple)):
            monitoring_data = []

        normalized_records = await database_sync_to_async(
            report_persistence.persist_notify_monitoring_report
        )(
            charger=self.consumer.charger,
            aggregate_charger=self.consumer.aggregate_charger,
            charger_id=getattr(self.consumer, "charger_id", None),
            connector_id=getattr(self.consumer, "connector_value", None),
            request_id=request_id,
            seq_no=seq_no,
            generated_at=generated_at,
            tbc=tbc,
            payload_data=payload_data,
            monitoring_data=list(monitoring_data),
        )
        received_at = timezone.now()
        for record in normalized_records:
            store.record_monitoring_report(
                record.get("charger_id"),
                request_id=record.get("request_id"),
                seq_no=record.get("seq_no"),
                generated_at=record.get("generated_at"),
                tbc=record.get("tbc", False),
                component_name=record.get("component_name", ""),
                component_instance=record.get("component_instance", ""),
                variable_name=record.get("variable_name", ""),
                variable_instance=record.get("variable_instance", ""),
                monitoring_id=record.get("monitoring_id"),
                severity=record.get("severity"),
                monitor_type=record.get("monitor_type", ""),
                threshold=record.get("threshold", ""),
                is_transaction=record.get("is_transaction", False),
                evse_id=record.get("evse_id"),
                connector_id=record.get("connector_id"),
                received_at=received_at,
            )
        if request_id is not None and not tbc:
            store.pop_monitoring_report_request(request_id)

        self.consumer._log_notify_monitoring_report(
            request_id=request_id,
            seq_no=seq_no,
            generated_at=generated_at,
            tbc=tbc,
            items=len(monitoring_data),
        )
        return {}
