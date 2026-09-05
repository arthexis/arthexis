"""Reconcile clearly historical OCPP transaction starts.

Some charge points buffer OCPP messages while disconnected and replay them once a
CSMS is reachable again. A stale StartTransaction should be recorded as history,
not subjected to the authorization policy that applies to a transaction starting
now.

The charger timestamp is telemetry rather than proof of age. A configurable
minimum age keeps ordinary clock skew and network latency on the live path.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from channels.db import database_sync_to_async
from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.cards.models import RFID as CoreRFID
from apps.cards.models import RFIDAttempt

from ... import store
from ...models import Transaction
from ...utils import _parse_ocpp_timestamp
from .identity import _extract_vehicle_identifier

logger = logging.getLogger(__name__)

DEFAULT_HISTORICAL_TRANSACTION_GRACE_SECONDS = 3600
HISTORICAL_AUTHORIZATION_POLICY = "historical"
HISTORICAL_AUTHORIZATION_REASON = "historical_transaction_reconciliation"
HISTORICAL_REPLAY_ID_PREFIX = "ocpp16-start:"


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
    return parsed < cutoff


def historical_replay_identity(msg_id: str | None, payload: dict[str, Any]) -> str:
    """Return a durable identity for an OCPP 1.6 historical start replay.

    CALL ids may be reused after reconnects, so stable StartTransaction fields are
    included in the digest. An exact retransmission still resolves to the same
    identity while a later transaction reusing the same CALL id does not.
    """

    normalized_msg_id = str(msg_id or "").strip()
    if not normalized_msg_id:
        return ""
    identity_payload = {
        "msg_id": normalized_msg_id,
        "connector_id": payload.get("connectorId"),
        "id_tag": str(payload.get("idTag") or ""),
        "meter_start": payload.get("meterStart"),
        "timestamp": str(payload.get("timestamp") or ""),
    }
    encoded = json.dumps(
        identity_payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{HISTORICAL_REPLAY_ID_PREFIX}{digest}"


async def _existing_rfid(id_tag: str) -> CoreRFID | None:
    """Return a previously known RFID without creating a new card record."""

    if not id_tag:
        return None

    def _lookup() -> CoreRFID | None:
        return CoreRFID.matching_queryset(id_tag).first()

    return await database_sync_to_async(_lookup)()


async def _existing_historical_transaction(
    consumer: Any,
    replay_identity: str,
) -> Transaction | None:
    """Return a previously reconciled historical start for this charger and CALL."""

    if not replay_identity:
        return None

    def _lookup() -> Transaction | None:
        return Transaction.objects.filter(
            charger=consumer.charger,
            ocpp_transaction_id=replay_identity,
            authorization_reason=HISTORICAL_AUTHORIZATION_REASON,
        ).first()

    return await database_sync_to_async(_lookup)()


def _historical_response(tx_obj: Transaction) -> dict[str, Any]:
    """Build the accepted OCPP 1.6 StartTransaction response for history."""

    return {
        "transactionId": tx_obj.pk,
        "idTagInfo": {
            "status": "Accepted",
            "authorizationPolicy": HISTORICAL_AUTHORIZATION_POLICY,
            "reason": HISTORICAL_AUTHORIZATION_REASON,
        },
    }


async def reconcile_historical_start_transaction(
    consumer: Any,
    payload: dict[str, Any],
    msg_id: str,
    text_data: str | None,
) -> dict[str, Any] | None:
    """Accept and persist a stale OCPP 1.6 ``StartTransaction`` replay.

    ``None`` means the message is recent (or has no trustworthy timestamp) and
    must continue through the normal live authorization path. Repeating the same
    historical CALL returns the original transaction id without creating a
    second transaction or RFID-attempt audit row.
    """

    start_timestamp = _parse_ocpp_timestamp(payload.get("timestamp"))
    received_start = timezone.now()
    if not is_historical_transaction_timestamp(start_timestamp, now=received_start):
        return None

    await consumer._assign_connector(payload.get("connectorId"))
    replay_identity = historical_replay_identity(msg_id, payload)
    existing = await _existing_historical_transaction(consumer, replay_identity)
    if existing is not None:
        store.transactions[consumer.store_key] = existing
        logger.info(
            "Reused historical OCPP StartTransaction charger=%s connector=%s transaction=%s",
            getattr(consumer, "charger_id", None) or consumer.store_key,
            payload.get("connectorId"),
            existing.pk,
        )
        return _historical_response(existing)

    id_tag = str(payload.get("idTag") or "").strip()
    account = await consumer._get_account(id_tag)

    # Retain an existing fallback-account association when the live policy would
    # have produced one, but never create an RFID merely because history was
    # replayed. The historical outcome itself remains Accepted regardless of the
    # current live authorization decision.
    tag = await _existing_rfid(id_tag)
    if tag is not None:
        decision = await consumer._evaluate_authorization_policy(
            id_tag=id_tag,
            account=account,
            tag=tag,
            tag_created=False,
        )
        if decision.reason == "rfid_fallback_account_authorized":
            account = await consumer._bind_fallback_account_for_decision(
                decision,
                tag=tag,
                account=account,
            )

    vid_value, vin_value = _extract_vehicle_identifier(payload)

    def _persist() -> tuple[Transaction, bool]:
        with db_transaction.atomic():
            type(consumer.charger).objects.select_for_update().only("pk").get(
                pk=consumer.charger.pk
            )
            if replay_identity:
                existing_tx = Transaction.objects.filter(
                    charger=consumer.charger,
                    ocpp_transaction_id=replay_identity,
                    authorization_reason=HISTORICAL_AUTHORIZATION_REASON,
                ).first()
                if existing_tx is not None:
                    return existing_tx, False
            return (
                Transaction.objects.create(
                    charger=consumer.charger,
                    account=account,
                    rfid=id_tag,
                    vid=vid_value,
                    vin=vin_value,
                    connector_id=payload.get("connectorId"),
                    meter_start=payload.get("meterStart"),
                    start_time=start_timestamp,
                    received_start_time=received_start,
                    ocpp_transaction_id=replay_identity,
                    authorization_status=Transaction.AuthorizationStatus.ACCEPTED,
                    authorization_reason=HISTORICAL_AUTHORIZATION_REASON,
                ),
                True,
            )

    tx_obj, created = await database_sync_to_async(_persist)()
    if created:
        await consumer._ensure_ocpp_transaction_identifier(tx_obj)

    # Keep the replay available for an immediately following historical
    # StopTransaction, but avoid live-start side effects such as auto-start
    # reservation changes, RFID enrollment, or consumption polling.
    store.transactions[consumer.store_key] = tx_obj
    if not created:
        return _historical_response(tx_obj)

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

    return _historical_response(tx_obj)
