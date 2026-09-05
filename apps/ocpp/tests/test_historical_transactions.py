from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from channels.db import database_sync_to_async
from django.test import override_settings
from django.utils import timezone

from apps.cards.models import RFID, RFIDAttempt
from apps.ocpp import store
from apps.ocpp.consumers.base.historical_transactions import (
    HISTORICAL_AUTHORIZATION_POLICY,
    HISTORICAL_AUTHORIZATION_REASON,
    is_historical_transaction_timestamp,
)
from apps.ocpp.consumers.csms.consumer import CSMSConsumer
from apps.ocpp.models import Charger, Transaction


async def _consumer(*, charger_id: str) -> CSMSConsumer:
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id=charger_id,
        authorization_policy=Charger.AuthorizationPolicy.STRICT,
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    async def fake_assign(connector):
        consumer.connector_value = connector

    consumer._assign_connector = AsyncMock(side_effect=fake_assign)
    consumer._start_consumption_updates = AsyncMock()
    return consumer


@override_settings(OCPP_HISTORICAL_TRANSACTION_GRACE_SECONDS=60)
def test_historical_timestamp_requires_more_than_grace_window():
    now = timezone.now()

    assert is_historical_transaction_timestamp(now - timedelta(seconds=61), now=now)
    assert not is_historical_transaction_timestamp(now - timedelta(seconds=59), now=now)
    assert not is_historical_transaction_timestamp(now + timedelta(seconds=1), now=now)
    assert not is_historical_transaction_timestamp(None, now=now)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
@override_settings(OCPP_HISTORICAL_TRANSACTION_GRACE_SECONDS=60)
async def test_historical_start_is_accepted_without_enrolling_unknown_rfid():
    consumer = await _consumer(charger_id="CP-HISTORICAL-START")
    sent_at = timezone.now() - timedelta(hours=2)

    result = await consumer._handle_start_transaction_action(
        {
            "idTag": "historical-unknown",
            "connectorId": 1,
            "meterStart": 1200,
            "timestamp": sent_at.isoformat(),
        },
        "msg-historical-start",
        "",
        "",
    )

    assert result["idTagInfo"] == {
        "status": "Accepted",
        "authorizationPolicy": HISTORICAL_AUTHORIZATION_POLICY,
        "reason": HISTORICAL_AUTHORIZATION_REASON,
    }

    tx = await database_sync_to_async(Transaction.objects.get)(
        charger=consumer.charger,
        rfid="historical-unknown",
    )
    assert tx.authorization_status == Transaction.AuthorizationStatus.ACCEPTED
    assert tx.authorization_reason == HISTORICAL_AUTHORIZATION_REASON
    assert tx.start_time == sent_at
    assert tx.received_start_time > tx.start_time
    assert result["transactionId"] == tx.pk

    assert not await database_sync_to_async(
        RFID.objects.filter(rfid="HISTORICAL-UNKNOWN").exists
    )()

    attempt = await database_sync_to_async(RFIDAttempt.objects.latest)("attempted_at")
    assert attempt.status == RFIDAttempt.Status.ACCEPTED
    assert attempt.transaction_id == tx.pk
    assert attempt.payload["authorization_policy"] == HISTORICAL_AUTHORIZATION_POLICY
    assert attempt.payload["authorization_reason"] == HISTORICAL_AUTHORIZATION_REASON

    store.transactions.pop(consumer.store_key, None)
    store.end_session_log(consumer.store_key)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
@override_settings(OCPP_HISTORICAL_TRANSACTION_GRACE_SECONDS=60)
async def test_recent_start_still_uses_live_authorization_policy():
    consumer = await _consumer(charger_id="CP-RECENT-START")

    result = await consumer._handle_start_transaction_action(
        {
            "idTag": "recent-unknown",
            "connectorId": 1,
            "meterStart": 1200,
            "timestamp": timezone.now().isoformat(),
        },
        "msg-recent-start",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Invalid"
    assert result["idTagInfo"]["reason"] == "strict_account_required"

    tx = await database_sync_to_async(Transaction.objects.get)(
        charger=consumer.charger,
        rfid="recent-unknown",
    )
    assert tx.authorization_status == Transaction.AuthorizationStatus.REJECTED
    assert tx.authorization_reason == "strict_account_required"
