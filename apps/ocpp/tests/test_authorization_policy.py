import pytest
from channels.db import database_sync_to_async
from django.core.cache import cache

from apps.cards.models import RFID, RFIDAttempt
from apps.energy.models import CustomerAccount
from apps.features.models import Feature
from apps.ocpp import store
from apps.ocpp.consumers.csms.consumer import CSMSConsumer
from apps.ocpp.models import Charger


@pytest.fixture
async def feature_cache_setup():
    await database_sync_to_async(cache.clear)()
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="energy-accounts",
        defaults={"display": "Energy Accounts", "is_enabled": False},
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
async def test_authorization_policy_strict_rejects_unknown_tag(
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

    assert result["idTagInfo"]["status"] == "Invalid"

    attempt = await database_sync_to_async(RFIDAttempt.objects.latest)("attempted_at")
    assert attempt.payload["authorization_reason"] == "strict_account_required"


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

    attempt = await database_sync_to_async(RFIDAttempt.objects.latest)("attempted_at")
    assert attempt.payload["authorization_reason"] == "open_policy_insecure_compatibility_mode"


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
