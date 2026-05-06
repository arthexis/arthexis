from unittest.mock import AsyncMock

import pytest
from channels.db import database_sync_to_async
from django.core.cache import cache

from apps.cards.models import RFID, RFIDAttempt
from apps.energy.models import CustomerAccount
from apps.features.models import Feature
from apps.ocpp import store
from apps.ocpp.consumers.base.rfid import RFID_FALLBACK_ACCOUNT_NAME
from apps.ocpp.consumers.csms.consumer import CSMSConsumer
from apps.ocpp.models import Charger, Transaction


@pytest.fixture
async def feature_cache_setup():
    await database_sync_to_async(cache.clear)()
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="energy-accounts",
        defaults={"display": "Energy Accounts", "is_enabled": False},
    )
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="rfid-fallback-account",
        defaults={"display": "RFID Fallback Account", "is_enabled": True},
    )


@pytest.fixture
def consumer_factory():
    async def _build(*, charger_id: str, policy: str) -> CSMSConsumer:
        charger = await database_sync_to_async(Charger.objects.create)(
            charger_id=charger_id,
            authorization_policy=policy,
        )
        consumer = CSMSConsumer(scope={}, receive=None, send=None)
        consumer.store_key = store.identity_key(charger.charger_id, 1)
        consumer.charger_id = charger.charger_id
        consumer.charger = charger
        consumer.aggregate_charger = None
        return consumer

    return _build


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_strict_accepts_unknown_tag_with_fallback_feature(
    feature_cache_setup,
    consumer_factory,
):
    consumer = await consumer_factory(
        charger_id="CP-POLICY-STRICT",
        policy=Charger.AuthorizationPolicy.STRICT,
    )

    result = await consumer._handle_authorize_action(
        {"idTag": "strict-unknown"},
        "msg-auth-policy-strict",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Accepted"

    attempt = await database_sync_to_async(RFIDAttempt.objects.latest)("attempted_at")
    assert attempt.payload["authorization_reason"] == "rfid_fallback_account_authorized"
    account = await database_sync_to_async(CustomerAccount.objects.get)(
        name=str(RFID_FALLBACK_ACCOUNT_NAME)
    )
    assert account.service_account is True
    assert attempt.account_id == account.pk
    assert await database_sync_to_async(account.rfids.filter(rfid="STRICT-UNKNOWN").exists)()


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_allowlist_accepts_known_tag(
    feature_cache_setup,
    consumer_factory,
):
    consumer = await consumer_factory(
        charger_id="CP-POLICY-ALLOW",
        policy=Charger.AuthorizationPolicy.ALLOWLIST,
    )
    await database_sync_to_async(RFID.objects.create)(
        rfid="ALLOW-001",
        allowed=True,
        released=True,
    )

    result = await consumer._handle_authorize_action(
        {"idTag": "ALLOW-001"},
        "msg-auth-policy-allow",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Accepted"

    attempt = await database_sync_to_async(RFIDAttempt.objects.latest)("attempted_at")
    assert attempt.payload["authorization_reason"] == "allowlist_tag_authorized"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_open_explicit_mode_accepts_and_auto_enrolls(
    feature_cache_setup,
    consumer_factory,
):
    consumer = await consumer_factory(
        charger_id="CP-POLICY-OPEN",
        policy=Charger.AuthorizationPolicy.OPEN,
    )

    result = await consumer._handle_authorize_action(
        {"idTag": "open-001"},
        "msg-auth-policy-open",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Accepted"
    tag = await database_sync_to_async(RFID.objects.get)(rfid="OPEN-001")
    assert tag.allowed is True
    assert tag.released is True
    assert tag.discovered_via_ocpp is True

    attempt = await database_sync_to_async(RFIDAttempt.objects.latest)("attempted_at")
    assert attempt.payload["authorization_reason"] == "open_policy_insecure_compatibility_mode"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_open_preserves_manual_discovery_flag(
    feature_cache_setup,
    consumer_factory,
):
    consumer = await consumer_factory(
        charger_id="CP-POLICY-OPEN-MANUAL",
        policy=Charger.AuthorizationPolicy.OPEN,
    )
    tag = await database_sync_to_async(RFID.objects.create)(
        rfid="OPEN-MANUAL",
        allowed=False,
        released=False,
        discovered_via_ocpp=False,
    )

    result = await consumer._handle_authorize_action(
        {"idTag": "OPEN-MANUAL"},
        "msg-auth-policy-open-manual",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Accepted"
    refreshed = await database_sync_to_async(RFID.objects.get)(pk=tag.pk)
    assert refreshed.allowed is True
    assert refreshed.released is True
    assert refreshed.discovered_via_ocpp is False


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_open_accepts_blocked_account(
    feature_cache_setup,
    consumer_factory,
):
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="energy-accounts",
        defaults={
            "display": "Energy Accounts",
            "is_enabled": True,
            "metadata": {"parameters": {"energy_credits_required": "enabled"}},
        },
    )
    consumer = await consumer_factory(
        charger_id="CP-POLICY-OPEN-BLOCKED",
        policy=Charger.AuthorizationPolicy.OPEN,
    )
    tag = await database_sync_to_async(RFID.objects.create)(
        rfid="OPEN-BLOCKED-001",
        allowed=True,
        released=True,
    )
    account = await database_sync_to_async(CustomerAccount.objects.create)(
        name="OPEN-BLOCKED-ACC"
    )
    await database_sync_to_async(account.rfids.add)(tag)

    result = await consumer._handle_authorize_action(
        {"idTag": "OPEN-BLOCKED-001"},
        "msg-auth-policy-open-blocked",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Accepted"
    attempt = await database_sync_to_async(RFIDAttempt.objects.latest)("attempted_at")
    assert attempt.payload["authorization_reason"] == "open_policy_insecure_compatibility_mode"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_strict_rejects_unknown_when_fallback_disabled(feature_cache_setup, consumer_factory):
    await database_sync_to_async(Feature.objects.update_or_create)(slug="rfid-fallback-account", defaults={"display": "RFID Fallback Account", "is_enabled": False})
    consumer = await consumer_factory(charger_id="CP-POLICY-STRICT-DISABLED", policy=Charger.AuthorizationPolicy.STRICT)
    result = await consumer._handle_authorize_action({"idTag": "strict-disabled"}, "msg-auth-policy-strict-disabled", "", "")
    assert result["idTagInfo"]["status"] == "Invalid"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_strict_rejects_blocked_tag_with_fallback(feature_cache_setup, consumer_factory):
    consumer = await consumer_factory(charger_id="CP-POLICY-STRICT-BLOCKED", policy=Charger.AuthorizationPolicy.STRICT)
    await database_sync_to_async(RFID.objects.create)(rfid="STRICT-BLOCKED", allowed=False, released=False)
    result = await consumer._handle_authorize_action({"idTag": "STRICT-BLOCKED"}, "msg-auth-policy-strict-blocked", "", "")
    assert result["idTagInfo"]["status"] == "Invalid"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_start_transaction_strict_fallback_binds_debt_account(
    feature_cache_setup,
    consumer_factory,
):
    consumer = await consumer_factory(
        charger_id="CP-POLICY-START-FALLBACK",
        policy=Charger.AuthorizationPolicy.STRICT,
    )

    async def fake_assign(connector):
        consumer.connector_value = connector

    consumer._assign_connector = AsyncMock(side_effect=fake_assign)
    consumer._start_consumption_updates = AsyncMock()

    result = await consumer._handle_start_transaction_action(
        {
            "idTag": "start-fallback",
            "connectorId": 1,
            "meterStart": 0,
            "timestamp": "2024-01-01T00:00:00Z",
        },
        "msg-start-fallback",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Accepted"
    account = await database_sync_to_async(CustomerAccount.objects.get)(
        name=str(RFID_FALLBACK_ACCOUNT_NAME)
    )
    tx = await database_sync_to_async(Transaction.objects.get)(
        charger=consumer.charger,
        rfid="start-fallback",
    )
    assert tx.account_id == account.pk
    attempt = await database_sync_to_async(RFIDAttempt.objects.latest)("attempted_at")
    assert attempt.account_id == account.pk
    assert attempt.transaction_id == tx.pk
    tag = await database_sync_to_async(RFID.objects.get)(rfid="START-FALLBACK")
    assert tag.discovered_via_ocpp is True
    assert await database_sync_to_async(account.rfids.filter(pk=tag.pk).exists)()


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_transaction_event_strict_fallback_binds_debt_account(
    feature_cache_setup,
    consumer_factory,
):
    consumer = await consumer_factory(
        charger_id="CP-POLICY-EVENT-FALLBACK",
        policy=Charger.AuthorizationPolicy.STRICT,
    )

    async def fake_assign(connector):
        consumer.connector_value = connector

    consumer._assign_connector = AsyncMock(side_effect=fake_assign)
    consumer._start_consumption_updates = AsyncMock()
    consumer._process_meter_value_entries = AsyncMock()

    result = await consumer._handle_transaction_event_action(
        {
            "eventType": "Started",
            "timestamp": "2024-01-01T00:00:00Z",
            "evse": {"id": 1, "connectorId": 1},
            "idToken": {"idToken": "event-fallback"},
            "transactionInfo": {"transactionId": "TX-EVENT-FALLBACK", "meterStart": 0},
        },
        "msg-event-fallback",
        "",
        "",
    )

    assert result["idTokenInfo"]["status"] == "Accepted"
    account = await database_sync_to_async(CustomerAccount.objects.get)(
        name=str(RFID_FALLBACK_ACCOUNT_NAME)
    )
    tx = await database_sync_to_async(Transaction.objects.get)(
        charger=consumer.charger,
        ocpp_transaction_id="TX-EVENT-FALLBACK",
    )
    assert tx.account_id == account.pk
    attempt = await database_sync_to_async(RFIDAttempt.objects.latest)("attempted_at")
    assert attempt.account_id == account.pk
    assert attempt.transaction_id == tx.pk
    tag = await database_sync_to_async(RFID.objects.get)(rfid="EVENT-FALLBACK")
    assert tag.discovered_via_ocpp is True
    assert await database_sync_to_async(account.rfids.filter(pk=tag.pk).exists)()
