import pytest
from channels.db import database_sync_to_async
from django.core.cache import cache

from apps.cards.models import RFID
from apps.features.models import Feature
from apps.ocpp import store
from apps.ocpp.consumers.csms.consumer import CSMSConsumer
from apps.ocpp.models import Charger


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_strict_rejects_unknown_tag():
    await database_sync_to_async(cache.clear)()
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="energy-accounts",
        defaults={"display": "Energy Accounts", "is_enabled": False},
    )
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-POLICY-STRICT",
        authorization_policy=Charger.AuthorizationPolicy.STRICT,
    )

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    result = await consumer._handle_authorize_action(
        {"idTag": "strict-unknown"},
        "msg-auth-policy-strict",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Invalid"
    assert result["idTagInfo"]["reason"] == "strict_account_required"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_allowlist_accepts_known_tag():
    await database_sync_to_async(cache.clear)()
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="energy-accounts",
        defaults={"display": "Energy Accounts", "is_enabled": False},
    )
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-POLICY-ALLOW",
        authorization_policy=Charger.AuthorizationPolicy.ALLOWLIST,
    )
    await database_sync_to_async(RFID.objects.create)(
        rfid="ALLOW-001",
        allowed=True,
        released=True,
    )

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    result = await consumer._handle_authorize_action(
        {"idTag": "ALLOW-001"},
        "msg-auth-policy-allow",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Accepted"
    assert result["idTagInfo"]["reason"] == "allowlist_tag_authorized"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_policy_open_explicit_mode_accepts_and_auto_enrolls():
    await database_sync_to_async(cache.clear)()
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="energy-accounts",
        defaults={"display": "Energy Accounts", "is_enabled": False},
    )
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-POLICY-OPEN",
        authorization_policy=Charger.AuthorizationPolicy.OPEN,
    )

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    result = await consumer._handle_authorize_action(
        {"idTag": "open-001"},
        "msg-auth-policy-open",
        "",
        "",
    )

    assert result["idTagInfo"]["status"] == "Accepted"
    assert result["idTagInfo"]["reason"] == "open_policy_insecure_compatibility_mode"
    tag = await database_sync_to_async(RFID.objects.get)(rfid="OPEN-001")
    assert tag.allowed is True
    assert tag.released is True
