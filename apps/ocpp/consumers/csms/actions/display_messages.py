"""Display message notification handlers for CSMS."""

from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.services import report_persistence
from apps.ocpp.utils import _parse_ocpp_timestamp, try_parse_int


class NotifyDisplayMessagesActionHandler:
    """Handle NotifyDisplayMessages payloads."""

    def __init__(self, consumer) -> None:
        self.consumer = consumer

    async def handle(self, payload, msg_id, _raw, _text_data) -> dict:
        payload_data = payload if isinstance(payload, dict) else {}
        request_id_value = payload_data.get("requestId")
        tbc_value = payload_data.get("tbc")
        request_id = try_parse_int(request_id_value)
        tbc = bool(tbc_value) if tbc_value is not None else False
        message_info = payload_data.get("messageInfo")
        if not isinstance(message_info, (list, tuple)):
            message_info = []

        received_at = timezone.now()
        compliance_messages = await database_sync_to_async(
            report_persistence.persist_notify_display_messages
        )(
            charger=self.consumer.charger,
            aggregate_charger=self.consumer.aggregate_charger,
            charger_id=getattr(self.consumer, "charger_id", None),
            connector_id=getattr(self.consumer, "connector_value", None),
            msg_id=msg_id,
            request_id=request_id,
            tbc=tbc,
            payload_data=payload_data,
            message_info=list(message_info),
            received_at=received_at,
            parse_timestamp=_parse_ocpp_timestamp,
        )

        store.record_display_message_compliance(
            self.consumer.charger_id,
            request_id=request_id,
            tbc=tbc,
            messages=compliance_messages,
            received_at=received_at,
        )
        return {}
