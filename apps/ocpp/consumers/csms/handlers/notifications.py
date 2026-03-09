"""Notification handlers for OCPP 2.x CSMS events."""

from __future__ import annotations

from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.utils import _parse_ocpp_timestamp
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel


def _parse_int(value: object | None) -> int | None:
    """Convert inbound event values to integer when possible."""

    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _clean_text(value: object | None) -> str | None:
    """Normalize event values to stripped optional strings."""

    text = str(value or "").strip()
    return text or None


class NotificationHandlersMixin:
    """Handle OCPP notification events forwarded to observability."""

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyEvent")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "NotifyEvent")
    async def _handle_notify_event_action(self, payload, msg_id, raw, text_data):
        payload_data = payload if isinstance(payload, dict) else {}
        event_entries = payload_data.get("eventData")

        generated_at = _parse_ocpp_timestamp(payload_data.get("generatedAt"))
        received_at = timezone.now()

        try:
            seq_no = int(payload_data.get("seqNo")) if "seqNo" in payload_data else None
        except (TypeError, ValueError):
            seq_no = None
        tbc = bool(payload_data.get("tbc")) if "tbc" in payload_data else False

        if not isinstance(event_entries, (list, tuple)):
            store.add_log(self.store_key, "NotifyEvent: missing eventData", log_type="charger")
            return {}

        forwarded = 0
        for entry in event_entries:
            if not isinstance(entry, dict):
                continue

            component = entry.get("component") if isinstance(entry.get("component"), dict) else {}
            variable = entry.get("variable") if isinstance(entry.get("variable"), dict) else {}
            evse = component.get("evse") if isinstance(component.get("evse"), dict) else {}

            event_timestamp = _parse_ocpp_timestamp(entry.get("timestamp"))
            if event_timestamp is None:
                event_timestamp = generated_at or received_at

            connector_value = evse.get("connectorId") if isinstance(evse, dict) else None
            normalized_event = {
                "charger_id": getattr(self, "charger_id", None) or self.store_key,
                "connector_id": store.connector_slug(
                    connector_value if connector_value is not None else getattr(self, "connector_value", None)
                ),
                "evse_id": _parse_int(evse.get("id")),
                "event_id": _parse_int(entry.get("eventId")),
                "event_type": _clean_text(entry.get("eventType")),
                "trigger": _clean_text(entry.get("trigger")),
                "severity": _parse_int(entry.get("severity")),
                "actual_value": _clean_text(entry.get("actualValue")),
                "cause": _clean_text(entry.get("cause")),
                "tech_code": _clean_text(entry.get("techCode")),
                "tech_info": _clean_text(entry.get("techInfo")),
                "cleared": bool(entry.get("cleared")) if "cleared" in entry else False,
                "transaction_id": _clean_text(entry.get("transactionId")),
                "variable_monitoring_id": _parse_int(entry.get("variableMonitoringId")),
                "component_name": _clean_text(component.get("name") if component else None),
                "component_instance": _clean_text(component.get("instance") if component else None),
                "variable_name": _clean_text(variable.get("name") if variable else None),
                "variable_instance": _clean_text(variable.get("instance") if variable else None),
                "generated_at": generated_at or received_at,
                "event_timestamp": event_timestamp,
                "seq_no": seq_no,
                "tbc": tbc,
                "received_at": received_at,
            }

            store.forward_event_to_observability(normalized_event)
            forwarded += 1

        details: list[str] = []
        if seq_no is not None:
            details.append(f"seqNo={seq_no}")
        details.append(f"events={forwarded}")
        if generated_at is not None:
            details.append(f"generatedAt={generated_at.isoformat()}")

        store.add_log(
            self.store_key,
            "NotifyEvent" + (": " + ", ".join(details) if details else ""),
            log_type="charger",
        )
        return {}
