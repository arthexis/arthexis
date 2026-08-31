"""Notification handlers for OCPP 2.x CSMS events."""

from __future__ import annotations

from apps.ocpp import store
from apps.ocpp.utils import _parse_ocpp_timestamp
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel


class NotificationHandlersMixin:
    """Handle OCPP notification events."""

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyEvent")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "NotifyEvent")
    async def _handle_notify_event_action(self, payload, msg_id, raw, text_data):
        payload_data = payload if isinstance(payload, dict) else {}
        event_entries = payload_data.get("eventData")

        generated_at = _parse_ocpp_timestamp(payload_data.get("generatedAt"))

        try:
            seq_no = int(payload_data.get("seqNo")) if "seqNo" in payload_data else None
        except (TypeError, ValueError):
            seq_no = None
        tbc = bool(payload_data.get("tbc")) if "tbc" in payload_data else False

        if not isinstance(event_entries, (list, tuple)):
            store.add_log(
                self.store_key, "NotifyEvent: missing eventData", log_type="charger"
            )
            return {}

        accepted_events = sum(1 for entry in event_entries if isinstance(entry, dict))

        details: list[str] = []
        if seq_no is not None:
            details.append(f"seqNo={seq_no}")
        details.append(f"events={accepted_events}")
        if generated_at is not None:
            details.append(f"generatedAt={generated_at.isoformat()}")

        store.add_log(
            self.store_key,
            "NotifyEvent" + (": " + ", ".join(details) if details else ""),
            log_type="charger",
        )
        return {}
