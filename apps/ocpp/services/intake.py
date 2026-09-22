"""Persist-and-ack services for ACK-oriented inbound OCPP actions."""

from django.db import transaction

from apps.events.services import publish_safely
from apps.ocpp.domain.notifications import record_notification
from apps.ocpp.models import Charger, InboundProtocolRequest, NotificationRecord
from apps.ocpp.services.replay import complete_with_result


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
    publish_safely(
        event_type="ocpp.data_transfer.received",
        producer="ocpp",
        payload={
            "notification_id": record.pk,
            "charger_id": record.charger_id,
            "action": record.action,
        },
    )
