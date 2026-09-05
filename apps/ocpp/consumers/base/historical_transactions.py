"""Reconcile clearly historical OCPP transaction starts.

Some charge points buffer OCPP messages while disconnected and replay them once a
CSMS is reachable again.  A stale StartTransaction should be recorded as history,
not subjected to the authorization policy that applies to a transaction starting
now.

The charger timestamp is telemetry rather than proof of age.  A configurable
minimum age keeps ordinary clock skew and network latency on the live path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from channels.db import database_sync_to_async
from django.conf import settings
from django.utils import timezone

from apps.cards.models import RFIDAttempt

from ... import store
from ...models import Transaction
from ...utils import _parse_ocpp_timestamp
from .identity import _extract_vehicle_identifier

logger = logging.getLogger(__name__)

DEFAULT_HISTORICAL_TRANSACTION_GRACE_SECONDS = 3600
HISTORICAL_AUTHORIZATION_POLICY = "historical"
HISTORICAL_AUTHORIZATION_REASON = "historical_transaction_reconciliation"


def historical_transaction_grace_seconds() -> float:
    """Return the minimum transaction age that qualifies as historical."""

    configured = getattr(
        settings,
        "OCPP_HISTORICAL_TRANSACTION_GRACE_SECONDS",
        DEFAULT_HISTORICAL_TRANSACTION_GRACE_SECONDS,
    )
    try:
        return max(0.0, float(configured))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid OCPP_HISTORICAL_TRANSACTION_GRACE_SECONDS=%r; using %s",
            configured,
            DEFAULT_HISTORICAL_TRANSACTION_GRACE_SECONDS,
        )
        return float(DEFAULT_HISTORICAL_TRANSACTION_GRACE_SECONDS)


def is_historical_transaction_timestamp(
    timestamp: datetime | str | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether ``timestamp`` is clearly older than the live grace window."""

    parsed = timestamp if isinstance(timestamp, datetime) else _parse_ocpp_timestamp(timestamp)
    if parsed is None:
        return False

    current = now or timezone.now()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    if timezone.is_naive(current):
        current = timezone.make_aware(current, timezone.get_current_timezone())

    cutoff = current - timedelta(seconds=historical_transaction_grace_seconds())
    return parsed <= cutoff


async def reconcile_historical_start_transaction(
    consumer: Any,
    payload: dict[str, Any],
    text_data: str | None,
) -> dict[str, Any] | None:
    """Accept and persist a stale OCPP 1.6 ``StartTransaction`` replay.

    ``None`` means the message is recent (or has no trustworthy timestamp) and
    must continue through the normal live authorization path.
    """

    start_timestamp = _parse_ocpp_timestamp(payload.get("timestamp"))
    received_start = timezone.now()
    if not is_historical_transaction_timestamp(start_timestamp, now=received_start):
        return None

    id_tag = str(payload.get("idTag") or "").strip()
    account = await consumer._get_account(id_tag)
    await consumer._assign_connector(payload.get("connectorId"))
    vid_value, vin_value = _extract_vehicle_identifier(payload)

    tx_obj = await database_sync_to_async(Transaction.objects.create)(
        charger=consumer.charger,
        account=account,
        rfid=id_tag,
        vid=vid_value,
        vin=vin_value,
        connector_id=payload.get("connectorId"),
        meter_start=payload.get("meterStart"),
        start_time=start_timestamp,
        received_start_time=received_start,
        authorization_status=Transaction.AuthorizationStatus.ACCEPTED,
        authorization_reason=HISTORICAL_AUTHORIZATION_REASON,
    )
    await consumer._ensure_ocpp_transaction_identifier(tx_obj)

    # Keep the replay available for an immediately following historical
    # StopTransaction, but avoid live-start side effects such as auto-start
    # reservation changes, RFID enrollment, or consumption polling.
    store.transactions[consumer.store_key] = tx_obj
    store.start_session_log(consumer.store_key, tx_obj.pk)
    if text_data:
        store.add_session_message(consumer.store_key, text_data)

    await consumer._record_rfid_attempt(
        rfid=id_tag,
        status=RFIDAttempt.Status.ACCEPTED,
        account=account,
        transaction=tx_obj,
        policy=HISTORICAL_AUTHORIZATION_POLICY,
        reason=HISTORICAL_AUTHORIZATION_REASON,
    )

    age_seconds = max(0, int((received_start - start_timestamp).total_seconds()))
    logger.info(
        "Accepted historical OCPP StartTransaction charger=%s connector=%s "
        "transaction=%s start=%s received=%s age_seconds=%s",
        getattr(consumer, "charger_id", None) or consumer.store_key,
        payload.get("connectorId"),
        tx_obj.pk,
        start_timestamp.isoformat(),
        received_start.isoformat(),
        age_seconds,
    )

    return {
        "transactionId": tx_obj.pk,
        "idTagInfo": {
            "status": "Accepted",
            "authorizationPolicy": HISTORICAL_AUTHORIZATION_POLICY,
            "reason": HISTORICAL_AUTHORIZATION_REASON,
        },
    }
