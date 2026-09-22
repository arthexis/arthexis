"""Persist-and-ack services for ACK-oriented inbound OCPP actions."""

import logging
from datetime import datetime

from django.db import transaction

from apps.events.services import publish_safely
from apps.ocpp.domain.notifications import (
    record_notification,
    record_operational_status,
)
from apps.ocpp.models import (
    Charger,
    InboundProtocolRequest,
    NotificationRecord,
    OperationalStatusRecord,
)
from apps.ocpp.services.replay import complete_with_result

logger = logging.getLogger(__name__)

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
