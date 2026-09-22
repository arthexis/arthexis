"""Persist-and-ack services for ACK-oriented inbound OCPP actions."""

import logging
from datetime import datetime

from django.db import transaction

from apps.events.services import publish_safely
from apps.ocpp.domain.notifications import (
    record_monitoring,
    record_notification,
    record_operational_status,
)
from apps.ocpp.models import (
    Charger,
    InboundProtocolRequest,
    MonitoringRecord,
    NotificationRecord,
    OperationalStatusRecord,
)
from apps.ocpp.services.replay import complete_with_result

logger = logging.getLogger(__name__)

_REPORT_ACTIONS = frozenset(
    {"NotifyMonitoringReport", "NotifyReport", "ReportChargingProfiles"}
)

_GENERIC_NOTIFICATION_RESPONSES: dict[str, dict[str, object]] = {
    "ClearedChargingLimit": {},
    "CostUpdated": {},
    "NotifyChargingLimit": {},
    "NotifyCustomerInformation": {},
    "NotifyDisplayMessages": {},
    "NotifyEVChargingNeeds": {"status": "Accepted"},
    "NotifyEVChargingSchedule": {"status": "Accepted"},
    "NotifyEvent": {},
    "ReservationStatusUpdate": {},
    "SecurityEventNotification": {},
}

_OPERATIONAL_STATUS_ACTIONS = {
    "DiagnosticsStatusNotification": OperationalStatusRecord.Kind.DIAGNOSTICS,
    "FirmwareStatusNotification": OperationalStatusRecord.Kind.FIRMWARE,
    "PublishFirmwareStatusNotification": OperationalStatusRecord.Kind.FIRMWARE,
    "LogStatusNotification": OperationalStatusRecord.Kind.LOG,
}


@transaction.atomic
def process_data_transfer(
    *,
    charger: Charger,
    payload: dict[str, object],
    replay_request: InboundProtocolRequest | None = None,
) -> dict[str, object]:
    """Persist DataTransfer intake and its ACK before scheduling secondary work."""
    vendor_id = payload.get("vendorId")
    if not isinstance(vendor_id, str) or not vendor_id:
        raise ValueError("vendorId is required")

    retained = record_notification(
        charger=charger,
        action="DataTransfer",
        payload=payload,
    )
    response = {"status": "Accepted"}
    if replay_request is not None:
        complete_with_result(replay_request, payload=response)

    transaction.on_commit(lambda: _publish_data_transfer(retained))
    return response


def _publish_data_transfer(record: NotificationRecord) -> None:
    try:
        publish_safely(
            event_type="ocpp.data_transfer.received",
            producer="ocpp",
            payload={
                "notification_id": record.pk,
                "charger_id": record.charger_id,
                "action": record.action,
            },
        )
    except Exception:
        logger.exception(
            "Could not enqueue secondary processing for DataTransfer %s",
            record.pk,
        )



def operational_status_kind(action: str) -> str | None:
    """Return the retained operational-status kind for one OCPP action."""
    return _OPERATIONAL_STATUS_ACTIONS.get(action)


@transaction.atomic
def process_operational_status(
    *,
    charger: Charger,
    action: str,
    payload: dict[str, object],
    replay_request: InboundProtocolRequest | None = None,
) -> dict[str, object]:
    """Persist operational status and its ACK before secondary processing."""
    kind = operational_status_kind(action)
    if kind is None:
        raise ValueError(f"Unsupported operational status action: {action}")
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        raise ValueError("status is required")

    retained = record_operational_status(
        charger=charger,
        kind=kind,
        status=status,
        source_action=action,
        payload=payload,
        reported_at=_optional_timestamp(payload.get("timestamp")),
    )
    response: dict[str, object] = {}
    if replay_request is not None:
        complete_with_result(replay_request, payload=response)

    transaction.on_commit(lambda: _publish_operational_status(retained))
    return response


def _publish_operational_status(record: OperationalStatusRecord) -> None:
    try:
        publish_safely(
            event_type="ocpp.operational_status.received",
            producer="ocpp",
            payload={
                "operational_status_id": record.pk,
                "charger_id": record.charger_id,
                "kind": record.kind,
                "action": record.source_action,
            },
        )
    except Exception:
        logger.exception(
            "Could not enqueue secondary processing for operational status %s",
            record.pk,
        )


def _optional_timestamp(value: object | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp is invalid") from error



def generic_notification_response(action: str) -> dict[str, object] | None:
    """Return the protocol ACK shape for one generic notification action."""
    response = _GENERIC_NOTIFICATION_RESPONSES.get(action)
    return dict(response) if response is not None else None


@transaction.atomic
def process_generic_notification(
    *,
    charger: Charger,
    action: str,
    payload: dict[str, object],
    replay_request: InboundProtocolRequest | None = None,
) -> dict[str, object]:
    """Persist one generic OCPP notification and its exact ACK atomically."""
    response = generic_notification_response(action)
    if response is None:
        raise ValueError(f"Unsupported generic notification action: {action}")

    retained = record_notification(
        charger=charger,
        action=action,
        payload=payload,
        reported_at=_notification_timestamp(payload),
    )
    if replay_request is not None:
        complete_with_result(replay_request, payload=response)

    transaction.on_commit(lambda: _publish_generic_notification(retained))
    return response


def _publish_generic_notification(record: NotificationRecord) -> None:
    try:
        publish_safely(
            event_type="ocpp.notification.received",
            producer="ocpp",
            payload={
                "notification_id": record.pk,
                "charger_id": record.charger_id,
                "action": record.action,
            },
        )
    except Exception:
        logger.exception(
            "Could not enqueue secondary processing for notification %s",
            record.pk,
        )


def _notification_timestamp(payload: dict[str, object]) -> datetime | None:
    direct = payload.get("timestamp")
    if direct is not None:
        return _optional_timestamp(direct)

    event_data = payload.get("eventData")
    if isinstance(event_data, list):
        timestamps = [
            _optional_timestamp(item.get("timestamp"))
            for item in event_data
            if isinstance(item, dict) and item.get("timestamp") is not None
        ]
        timestamps = [item for item in timestamps if item is not None]
        if timestamps:
            return max(timestamps)
    return None




def is_report_action(action: str) -> bool:
    """Return whether an inbound action belongs to the retained report family."""
    return action in _REPORT_ACTIONS


@transaction.atomic
def process_report_intake(
    *,
    charger: Charger,
    action: str,
    payload: dict[str, object],
    replay_request: InboundProtocolRequest | None = None,
) -> dict[str, object]:
    """Persist one report chunk and its empty ACK atomically."""
    if not is_report_action(action):
        raise ValueError(f"Unsupported report action: {action}")

    reported_at = _report_timestamp(payload)
    if action in {"NotifyMonitoringReport", "NotifyReport"}:
        retained = record_monitoring(
            charger=charger,
            event_type=action,
            payload=payload,
            reported_at=reported_at,
        )
        record_type = "monitoring"
    else:
        retained = record_notification(
            charger=charger,
            action=action,
            payload=payload,
            reported_at=reported_at,
        )
        record_type = "notification"

    response: dict[str, object] = {}
    if replay_request is not None:
        complete_with_result(replay_request, payload=response)

    transaction.on_commit(
        lambda: _publish_report_intake(
            retained=retained,
            action=action,
            record_type=record_type,
            payload=payload,
        )
    )
    return response



def _publish_report_intake(
    *,
    retained: MonitoringRecord | NotificationRecord,
    action: str,
    record_type: str,
    payload: dict[str, object],
) -> None:
    try:
        publish_safely(
            event_type="ocpp.report.received",
            producer="ocpp",
            payload={
                "record_id": retained.pk,
                "record_type": record_type,
                "charger_id": retained.charger_id,
                "action": action,
                "request_id": payload.get("requestId"),
                "seq_no": payload.get("seqNo"),
                "tbc": payload.get("tbc"),
            },
        )
    except Exception:
        logger.exception(
            "Could not enqueue secondary processing for report %s:%s",
            record_type,
            retained.pk,
        )



def _report_timestamp(payload: dict[str, object]) -> datetime | None:
    for name in ("generatedAt", "timestamp"):
        if payload.get(name) is not None:
            return _optional_timestamp(payload[name])
    return None
