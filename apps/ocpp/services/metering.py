"""Durable metering intake services."""

import logging
from datetime import datetime

from django.db import transaction

from apps.events.services import publish_safely
from apps.ocpp.models import Charger, InboundProtocolRequest, MeterReadingBatch
from apps.ocpp.services.replay import complete_with_result

logger = logging.getLogger(__name__)


@transaction.atomic
def process_v201_standalone_meter_values(
    *,
    charger: Charger,
    payload: dict[str, object],
    replay_request: InboundProtocolRequest | None = None,
) -> dict[str, object]:
    """Persist standalone OCPP 2.0.1 meter evidence and its ACK atomically."""
    if isinstance(payload.get("transactionInfo"), dict):
        raise ValueError("transaction-bound MeterValues require transaction intake")
    meter_values = payload.get("meterValue")
    if not isinstance(meter_values, list) or not meter_values:
        raise ValueError("meterValue must be a non-empty list")

    reported_at: datetime | None = None
    for meter_value in meter_values:
        if not isinstance(meter_value, dict):
            raise ValueError("Each meter value must be an object")
        sampled_values = meter_value.get("sampledValue")
        if not isinstance(sampled_values, list) or not sampled_values:
            raise ValueError("sampledValue must be a non-empty list")
        for sampled_value in sampled_values:
            if not isinstance(sampled_value, dict):
                raise ValueError("Each sampled value must be an object")
            if "value" not in sampled_value:
                raise ValueError("sampled value is required")
        timestamp = _optional_timestamp(meter_value.get("timestamp"))
        if timestamp is not None:
            reported_at = timestamp if reported_at is None else max(reported_at, timestamp)

    evse_id = payload.get("evseId")
    if isinstance(evse_id, bool) or (evse_id is not None and not isinstance(evse_id, int)):
        raise ValueError("evseId must be an integer")

    retained = MeterReadingBatch.objects.create(
        charger=charger,
        protocol="ocpp2.0.1",
        evse_id=evse_id,
        reported_at=reported_at,
        payload=payload,
    )
    response: dict[str, object] = {}
    if replay_request is not None:
        complete_with_result(replay_request, payload=response)

    transaction.on_commit(lambda: _publish_meter_batch(retained))
    return response


def _publish_meter_batch(batch: MeterReadingBatch) -> None:
    try:
        publish_safely(
            event_type="ocpp.meter_values.received",
            producer="ocpp",
            payload={
                "meter_batch_id": batch.pk,
                "charger_id": batch.charger_id,
                "transaction_id": None,
                "evse_id": batch.evse_id,
            },
        )
    except Exception:
        logger.exception(
            "Could not enqueue secondary processing for meter batch %s",
            batch.pk,
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
